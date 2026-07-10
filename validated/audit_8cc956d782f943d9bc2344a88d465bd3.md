### Title
Merkle Internal-Node Accepted as Transaction Leaf in `verify_transaction_inclusion` Enables Proof-Verification Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) passes the caller-supplied `tx_id` directly into `compute_root_from_merkle_proof` without validating that the proof length equals the full Merkle tree depth. Because `compute_root_from_merkle_proof` is a pure positional iterator that accepts any starting level, an attacker can supply an **internal Merkle tree node** as `tx_id` together with a **shortened proof** that begins at that node's level. The function computes the correct root and returns `true` for a Bitcoin transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` is a public, unpermissioned NEAR method (only gated by `#[pause]`, no `#[trusted_relayer]`). Its sole proof check is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

`compute_root_from_merkle_proof` is a simple positional iterator:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;
}
``` [2](#0-1) 

It performs exactly `merkle_proof.len()` iterations regardless of the actual tree depth. There is **no check** that `merkle_proof.len()` equals `ceil(log₂(n))` (the full depth for a block with `n` transactions). This means the function is indistinguishable between:

- A real leaf at depth `d` with a proof of length `d`
- An internal node at level `h` with a proof of length `d − h` (starting from that node's level)

Both produce the correct Merkle root. The function cannot tell them apart.

The function's own docstring acknowledges this but defers responsibility upward:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification." [3](#0-2) 

Despite being marked `#[deprecated(since = "0.5.0")]`, the function remains a fully callable on-chain entrypoint — Rust's `#[deprecated]` is a compiler warning only, not a runtime restriction. [4](#0-3) 

`verify_transaction_inclusion_v2` closes this gap by requiring `merkle_proof.len() == coinbase_merkle_proof.len()` and validating the coinbase proof first, which anchors the expected tree depth. But v1 has no such constraint and remains independently callable. [5](#0-4) 

---

### Impact Explanation

Any NEAR contract (e.g., a BTC bridge, a cross-chain settlement layer) that calls `verify_transaction_inclusion` v1 to gate fund releases or state transitions can be deceived into accepting a Bitcoin transaction that was never broadcast or confirmed. The attacker does not need to mine a block or control a relayer — they only need to know the Merkle tree structure of any already-confirmed block (public information from any Bitcoin node or block explorer).

A concrete bridge attack:
1. Bridge contract calls `verify_transaction_inclusion` to confirm a BTC deposit before releasing wrapped tokens.
2. Attacker supplies an internal node from a real confirmed block as `tx_id`, with a shortened proof.
3. `verify_transaction_inclusion` returns `true`.
4. Bridge releases tokens for a deposit that never happened.

The corrupted state (tokens released, transaction marked as processed) persists permanently — analogous to the original report's "stays bricked even after the malicious node leaves."

---

### Likelihood Explanation

The entry path requires no privileges. `verify_transaction_inclusion` accepts `ProofArgs` from any caller:

```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
``` [6](#0-5) 

All inputs are attacker-controlled. The only prerequisite is identifying a real confirmed block in the contract's mainchain (trivially obtained from `get_last_n_blocks_hashes` or `get_block_hash_by_height`) and computing its Merkle tree structure from public Bitcoin data. Likelihood is **high** for any deployment where a downstream contract calls v1.

---

### Recommendation

1. **Remove or hard-disable `verify_transaction_inclusion` v1** at the contract level (e.g., always panic) rather than relying on a compiler-only `#[deprecated]` annotation.
2. If v1 must remain for backward compatibility, add a proof-depth guard: require `args.merkle_proof.len()` to equal the expected tree depth derived from the block's transaction count, or validate a coinbase proof of the same length (as v2 does).
3. All downstream contracts must be migrated to `verify_transaction_inclusion_v2`.

---

### Proof of Concept

Given a real confirmed Bitcoin block `B` with `n = 8` transactions (tree depth `d = 3`) already tracked by the contract:

1. Fetch the Merkle tree of `B` from any public Bitcoin API.
2. Pick the internal node `N` at level `h = 1`, position `p = 0` (i.e., `hash(tx[0], tx[1])`).
3. The sibling of `N` at level 1 is `S = hash(tx[2], tx[3])`.
4. The parent of `N` and `S` at level 2 is `P = hash(N, S)`.
5. The sibling of `P` at level 2 is `Q = hash(tx[4..7])`.
6. Call `verify_transaction_inclusion` with:
   - `tx_id = N`
   - `tx_block_blockhash = B`
   - `tx_index = 0` (position `p = 0` at level 1)
   - `merkle_proof = [S, Q]` (length 2, not 3)
   - `confirmations = 1`
7. `compute_root_from_merkle_proof(N, 0, [S, Q])`:
   - Iteration 1: `hash(N, S) = P`, position → 0
   - Iteration 2: `hash(P, Q) = merkle_root`, position → 0
8. Result equals `header.block_header.merkle_root` → function returns **`true`**.

`N` is not a transaction. No such transaction exists. The function has been forged. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** merkle-tools/src/lib.rs (L34-52)
```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
}
```

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

### Title
Deprecated `verify_transaction_inclusion` Remains Callable, Bypassing Coinbase Merkle Proof Forgery Protection Enforced by `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

The contract exposes two public transaction-inclusion verification functions with **inconsistent validation levels**. `verify_transaction_inclusion_v2` enforces a coinbase merkle proof check to prevent 64-byte transaction forgery. The older `verify_transaction_inclusion` (v1) does not. Despite being marked `#[deprecated]`, v1 remains a live, callable NEAR entry point. Any unprivileged caller — or any consumer contract — can invoke v1 directly, bypassing the forgery protection that v2 enforces, and receive a forged `true` result for a non-existent transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (documented at https://www.bitmex.com/blog/64-Byte-Transactions). It does so by first verifying a coinbase merkle proof against the block's stored `merkle_root`, which anchors the proof depth and prevents an attacker from supplying an internal Merkle tree node as a `tx_id`: [1](#0-0) 

`verify_transaction_inclusion` (v1) performs no such coinbase check. It accepts a `tx_id` and a `merkle_proof`, computes the root, and returns `true` if it matches the stored `merkle_root` — with no constraint that `tx_id` is a leaf-level transaction hash: [2](#0-1) 

Rust's `#[deprecated]` attribute is a **compiler-only warning**. It does not remove the function from the compiled WASM binary or prevent NEAR runtime dispatch. The function remains a fully reachable public entry point: [3](#0-2) 

The `ProofArgs` struct accepted by v1 has no `coinbase_tx_id` or `coinbase_merkle_proof` fields, so the coinbase check cannot be performed even in principle: [4](#0-3) 

`compute_root_from_merkle_proof` in `merkle-tools` is a pure hash-chain computation with no awareness of tree depth or leaf vs. internal node distinction: [5](#0-4) 

This is the exact M-10 pattern: two code paths operate on the same verified state (the stored `merkle_root` in `headers_pool`) with different validation levels. The stricter path (v2) enforces the coinbase anchor; the weaker path (v1) does not. An attacker uses the weaker path to bypass the protection the stricter path enforces.

---

### Impact Explanation

A consumer contract or bridge that calls `verify_transaction_inclusion` (v1) can be deceived into accepting a forged transaction inclusion proof. An attacker constructs a 64-byte value that is a valid internal Merkle tree node, passes it as `tx_id` with a crafted `merkle_proof`, and receives `true` — falsely proving that a non-existent transaction was included in a confirmed Bitcoin block. Any downstream logic that gates fund releases, cross-chain swaps, or state transitions on this result is exploitable.

---

### Likelihood Explanation

The entry point is reachable by any unprivileged NEAR account with no staking, role, or deposit requirement beyond the `#[pause]` gate (which is not paused in normal operation). Consumer contracts that integrated before v2 was introduced, or that were written against the v1 ABI, call v1 directly. The 64-byte forgery technique is well-documented and has known tooling. Likelihood is high for any deployment whose consumers have not migrated to v2.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the contract entirely, or replace its body with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")`. A Rust `#[deprecated]` attribute provides zero on-chain enforcement. As long as v1 exists in the WASM binary, it is callable. The fix in v2 is correct; the gap is that v1 was not simultaneously disabled.

---

### Proof of Concept

1. Identify a confirmed Bitcoin block `B` with `merkle_root = R` stored in `headers_pool`.
2. Construct a 64-byte value `N` that is a valid internal Merkle tree node of `B` (i.e., `N = SHA256d(left_child || right_child)` for some subtree of `B`'s transaction tree).
3. Compute a `merkle_proof` path from `N` up to `R` (this is a valid partial Merkle path since `N` is an internal node).
4. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_block_blockhash = B`, `tx_index = <index of N's subtree>`, `merkle_proof = <path from N to R>`, `confirmations = 1`.
5. The function computes `compute_root_from_merkle_proof(N, index, proof) == R` → returns `true`.
6. The same call to `verify_transaction_inclusion_v2` would fail at the coinbase proof check, rejecting the forged proof. [6](#0-5) [7](#0-6)

### Citations

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

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
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

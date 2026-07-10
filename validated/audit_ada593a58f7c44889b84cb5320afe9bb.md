### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Proof Validation — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` was superseded by `verify_transaction_inclusion_v2`, which adds a mandatory coinbase Merkle proof check to prevent the 64-byte transaction Merkle proof forgery attack. However, the deprecated function is still marked `pub` and `#[pause]`, making it directly callable by any unprivileged NEAR account. The `#[deprecated]` Rust attribute is a compiler hint only — it imposes no on-chain access restriction. Any caller can invoke the deprecated path, skip the coinbase proof validation entirely, and obtain a `true` verification result for a forged transaction inclusion proof.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability documented at https://www.bitmex.com/blog/64-Byte-Transactions. It first validates a coinbase Merkle proof before delegating to the deprecated function:

```rust
// verify_transaction_inclusion_v2 — lib.rs lines 347-369
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
```

The deprecated function itself performs only a single Merkle root recomputation with no coinbase anchor:

```rust
// verify_transaction_inclusion — lib.rs lines 318-323
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

Because `verify_transaction_inclusion` is still declared `pub` and `#[pause]`, it is a live, callable NEAR contract method. The deprecation marker is a Rust compiler warning; it does not remove the method from the contract's ABI or restrict on-chain invocation in any way.

The analog to the external report is direct: `verify_transaction_inclusion_v2` is the "per-entity-specific" (coinbase-anchored) verification path, while `verify_transaction_inclusion` is the "global" (unanchored) path. Just as `calculateMinFeeWei` silently ignores per-user custom fees, `verify_transaction_inclusion` silently ignores the coinbase proof requirement — and callers can reach it directly, bypassing the guard entirely.

---

### Impact Explanation

A recipient contract or off-chain consumer that calls `verify_transaction_inclusion` directly — or that is directed to do so by an attacker — can be made to accept a forged transaction inclusion proof. In the 64-byte attack, an adversary supplies a `tx_id` that is actually the hash of an internal Merkle tree node (not a real transaction). `compute_root_from_merkle_proof` will reconstruct the correct Merkle root from this internal node, and the function returns `true`. Any downstream logic that gates fund releases, cross-chain state transitions, or authorization decisions on this result is compromised.

The contract's own warning acknowledges the risk:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [1](#0-0) 

---

### Likelihood Explanation

The 64-byte transaction Merkle proof forgery is a well-documented, practical attack with published tooling. No privileged role, leaked key, or social engineering is required — any NEAR account can call `verify_transaction_inclusion` directly. The function is not gated by any role check beyond the global `#[pause]` flag (which is controlled by the protocol, not the attacker). The attacker only needs to identify a block in the mainchain, locate an internal Merkle node, and submit a crafted `ProofArgs`. [2](#0-1) 

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion`, or convert it to a private helper used only by `verify_transaction_inclusion_v2`. If backward compatibility must be preserved, add a runtime `env::panic_str` at the top of the deprecated function body to prevent on-chain invocation. The coinbase proof validation must not be bypassable by choosing which public entry point to call.

---

### Proof of Concept

1. Identify any block `B` stored in the contract's mainchain with a known Merkle tree of at least two transactions.
2. Compute the hash of an internal Merkle tree node `N` (e.g., the parent of the first two leaf hashes). This 32-byte value is a valid `H256`.
3. Construct a `ProofArgs` with:
   - `tx_id` = hash of internal node `N`
   - `tx_block_blockhash` = block hash of `B`
   - `tx_index` = the index position of `N` within the Merkle tree level where it appears
   - `merkle_proof` = the sibling hashes needed to reconstruct the Merkle root from `N`
   - `confirmations` = any value ≤ `gc_threshold`
4. Call `verify_transaction_inclusion` (the deprecated public method) with this `ProofArgs`.
5. `compute_root_from_merkle_proof(N, index, proof)` reconstructs the correct Merkle root of block `B`, so the function returns `true` — confirming inclusion of a transaction that does not exist.

`verify_transaction_inclusion_v2` would reject this call at step 4 because the coinbase proof would not anchor to the real coinbase transaction. The deprecated path has no such guard. [3](#0-2) [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L276-281)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
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

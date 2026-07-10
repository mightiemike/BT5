### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable On-Chain, Bypassing Coinbase Merkle Proof Validation — (File: `contract/src/lib.rs`)

---

### Summary

The contract introduced `verify_transaction_inclusion_v2` to mitigate the well-known 64-byte transaction Merkle proof forgery attack. The v2 function enforces a coinbase Merkle proof check before proceeding. However, the original `verify_transaction_inclusion` function remains fully callable on-chain with no access restriction. The Rust `#[deprecated]` attribute is a compile-time hint only — it does not prevent any NEAR caller from invoking the function by name. Any unprivileged caller can bypass the coinbase validation entirely by calling the deprecated v1 endpoint directly.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (referenced in the function's own doc comment). It enforces the invariant that the coinbase transaction's Merkle proof must be validated first: [1](#0-0) 

The original `verify_transaction_inclusion` performs no such check — it only verifies that the supplied `tx_id` hashes up to the block's `merkle_root` via the provided proof path: [2](#0-1) 

The `#[deprecated]` attribute on the v1 function: [3](#0-2) 

...is a Rust compiler warning. It has zero effect on NEAR on-chain dispatch. The function remains a live, publicly accessible entry point with only a `#[pause]` guard (no role restriction): [4](#0-3) 

The asymmetry is exact: `verify_transaction_inclusion_v2` enforces the coinbase proof invariant; `verify_transaction_inclusion` does not — mirroring the external report's pattern where `onQueueWithdrawal` enforced the fixed-term restriction but `onSetAnnualInterestAndReserveRatioBips` did not.

---

### Impact Explanation

The 64-byte transaction attack allows an attacker to craft a `tx_id` that is the hash of an internal Merkle tree node rather than a real leaf transaction. Because the v1 function only checks that the supplied hash traverses to the correct `merkle_root`, it returns `true` for such a forged input. Any consuming contract or application that calls `verify_transaction_inclusion` to gate a security-critical action (e.g., releasing bridged funds upon BTC confirmation) can be deceived into accepting a fabricated proof of transaction inclusion. [5](#0-4) 

---

### Likelihood Explanation

- The function is publicly callable with no role restriction and no deposit requirement.
- The attack technique is fully documented and the contract's own codebase acknowledges it.
- No privileged access, leaked keys, or social engineering is required — any NEAR account can call `verify_transaction_inclusion` directly.
- Consuming contracts that integrated before v2 was introduced, or that did not update their call target, are silently exposed.

---

### Recommendation

Remove `verify_transaction_inline` from the public ABI entirely, or gate it behind a role (e.g., `Role::DAO`) so it cannot be called by unprivileged accounts. The preferred fix is deletion: `verify_transaction_inclusion_v2` is the correct, safe replacement and the v1 function provides no functionality that v2 does not already cover.

---

### Proof of Concept

1. Attacker selects any confirmed Bitcoin block whose Merkle tree has more than one transaction.
2. Attacker computes the hash of an internal Merkle node (e.g., the hash of two leaf hashes concatenated — exactly 64 bytes, triggering the known forgery).
3. Attacker constructs a valid Merkle path from that internal node hash up to the block's `merkle_root`.
4. Attacker calls `verify_transaction_inclusion` on the NEAR contract with:
   - `tx_id` = the internal node hash (not a real transaction)
   - `tx_block_blockhash` = the real block hash (already in the contract's mainchain)
   - `merkle_proof` = the crafted path
   - `confirmations` = any value ≤ `gc_threshold`
5. The function computes `compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof)` and compares it to `header.block_header.merkle_root`.
6. The comparison succeeds and the function returns `true` — a forged transaction inclusion proof accepted as valid. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L270-280)
```rust
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-289)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
```

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L358-365)
```rust
        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );
```

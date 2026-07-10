### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Merkle Proof Forgery Protection — (File: `contract/src/lib.rs`)

---

### Summary

The SPV proof verification logic is split across two public functions. The security-critical coinbase proof check — added specifically to mitigate the well-known 64-byte transaction Merkle proof forgery attack — exists only in `verify_transaction_inclusion_v2`, while the deprecated `verify_transaction_inclusion` remains a fully reachable public entry point with no such guard. Any unprivileged NEAR caller can invoke the deprecated path directly, obtaining a forged `true` result for a transaction that does not exist in the claimed block.

---

### Finding Description

The verification flow for SPV proofs is scattered across two public functions in `contract/src/lib.rs`:

**`verify_transaction_inclusion` (deprecated, lines 288–323):** [1](#0-0) 

This function performs: confirmations-vs-gc_threshold check, main-chain membership check via `mainchain_header_to_height`, non-empty proof check, and finally the Merkle root comparison. It has **no coinbase proof check**.

**`verify_transaction_inclusion_v2` (lines 347–369):** [2](#0-1) 

This function adds the coinbase proof check (lines 358–365) and then delegates to the deprecated function via `self.verify_transaction_inclusion(args.into())`. The coinbase check is the sole mitigation for the 64-byte transaction Merkle proof forgery vulnerability explicitly cited in the doc comment.

The critical structural problem is that the security-critical coinbase check lives **only** in v2, while v1 is still decorated with `#[pause]` and exposed as a public `#[near]` method — not `#[private]`. The `#[deprecated]` attribute is a Rust compiler hint only; it does not prevent on-chain invocation. Any NEAR account can call `verify_transaction_inclusion` directly, completely skipping the coinbase proof guard.

The warning in the v1 doc comment acknowledges the consequence: [3](#0-2) 

---

### Impact Explanation

An attacker supplies a `tx_id` that is the hash of an **internal Merkle tree node** (not a real transaction). Because the 64-byte node hash can be crafted to satisfy the Merkle root equation without a corresponding on-chain transaction, `verify_transaction_inclusion` returns `true`. Any downstream NEAR contract that calls this function to gate fund releases, cross-chain bridges, or other trust-sensitive operations will accept the forged proof as valid. The corrupted value is the **SPV proof result**: a false `true` for a non-existent transaction.

---

### Likelihood Explanation

- The function is unconditionally public; no role, stake, or privileged key is required.
- The 64-byte transaction forgery attack is well-documented and has known tooling.
- The split verification pattern (coinbase check in v2, everything else in v1) makes it easy to miss that v1 is still reachable.
- Likelihood is **high** for any deployment where downstream contracts consume `verify_transaction_inclusion` results.

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or annotate it with `#[private]` so it can only be called by the contract itself (i.e., as an internal helper for v2). Alternatively, inline the coinbase check into the shared logic so it cannot be bypassed regardless of which entry point is used. The scattered verification pattern — where one public function delegates to another public function that lacks a security-critical check — is the root cause and should be consolidated.

---

### Proof of Concept

1. Obtain a real Bitcoin block header that is on the contract's main chain (so `mainchain_header_to_height` lookup succeeds).
2. Construct an internal Merkle tree node hash `fake_tx_id` such that `compute_root_from_merkle_proof(fake_tx_id, index, proof) == block.merkle_root`. This is the standard 64-byte CVE construction.
3. Call `verify_transaction_inclusion` (not v2) directly from any NEAR account:
   ```
   verify_transaction_inclusion({
     tx_id: fake_tx_id,
     tx_block_blockhash: <valid mainchain block hash>,
     tx_index: <crafted index>,
     merkle_proof: <crafted proof>,
     confirmations: 1
   })
   ```
4. The function returns `true` — no coinbase proof was ever checked.
5. Any contract consuming this result treats the forged transaction as confirmed.

The reachable entry path is `verify_transaction_inclusion` at: [4](#0-3) 

The missing guard (present only in v2) is: [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L288-323)
```rust
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

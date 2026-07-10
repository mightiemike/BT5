### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Allowing Any Caller to Bypass 64-Byte Transaction Forgery Protection - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) was deprecated in favour of `verify_transaction_inclusion_v2`, which adds a mandatory coinbase Merkle proof check to defeat the 64-byte transaction forgery attack. However, v1 carries no access restriction and remains a live, callable public entry point. Any unprivileged NEAR caller — including a malicious proof submitter or a recipient contract — can invoke v1 directly, skipping the coinbase validation entirely and obtaining a `true` SPV result for a fabricated transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). It enforces that the coinbase transaction at index 0 is proven against the same Merkle root before the target transaction proof is evaluated. [1](#0-0) 

The old v1 function, however, is still a fully public `#[pause]`-gated method with no additional restriction. Its own doc-comment explicitly warns that it may return `true` for an internal Merkle tree node rather than a real transaction hash, and that higher-level validation is assumed — an assumption that is never enforced on-chain. [2](#0-1) 

The `#[deprecated]` attribute is a Rust compiler hint only; it does not prevent the function from being called at runtime. The NEAR runtime exposes every `pub` method as a callable entry point regardless of deprecation status. [3](#0-2) 

The structural parallel to the BridgeFacet report is exact: just as `execute()` could be called before `forceUpdateSlippage()` to bypass the user's intended destination slippage, here a caller can invoke v1 instead of v2 to bypass the coinbase proof check that was the entire motivation for introducing v2.

---

### Impact Explanation

An attacker who controls a 64-byte payload that is a valid SHA-256d pre-image of an internal Merkle tree node can supply that payload as `tx_id` to `verify_transaction_inclusion`. The function computes `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof)` and compares the result to the stored `merkle_root`. [4](#0-3) 

Because v1 performs no coinbase anchor check, the attacker can construct a valid-looking Merkle path from an internal node to the root, causing the function to return `true` for a transaction that was never included in any Bitcoin block. Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate a withdrawal, mint, or other high-value action will accept the forged proof.

---

### Likelihood Explanation

The 64-byte transaction forgery technique is publicly documented and has known tooling. The entry path requires no privileged role, no leaked key, and no social engineering — only the ability to call a public NEAR contract method and supply crafted arguments. Any relayer, bridge user, or third-party contract integrating the light client for SPV verification is a realistic trigger.

---

### Recommendation

Remove the `#[pause]` attribute from `verify_transaction_inclusion` and replace it with a hard `env::panic_str("use verify_transaction_inclusion_v2")` body, or delete the function entirely. If backward compatibility must be preserved for a migration window, gate the function with an explicit access-control role so that only trusted callers can invoke it, and document that it is insecure for general use.

---

### Proof of Concept

1. Deploy the contract with `feature = "bitcoin"` and a known mainchain header at height H whose `merkle_root` is `R`.
2. Identify an internal Merkle tree node `N` such that `compute_root_from_merkle_proof(N, idx, proof) == R` for some attacker-chosen `idx` and `proof`. (This is the standard 64-byte CVE construction.)
3. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the forged internal node, not a real txid)
   - `tx_block_blockhash` = the hash of the block at height H
   - `tx_index = idx`
   - `merkle_proof = proof`
   - `confirmations = 1`
4. The function returns `true`.
5. Calling `verify_transaction_inclusion_v2` with the same `tx_id` and any `coinbase_merkle_proof` would fail at the coinbase anchor check, demonstrating that v2 correctly rejects the forgery while v1 does not. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L277-323)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
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

**File:** contract/src/lib.rs (L347-368)
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
```

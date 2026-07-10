### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Coinbase Merkle Validation, Enabling SPV Proof Forgery - (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes a deprecated `verify_transaction_inclusion` function that omits coinbase Merkle proof validation. Any unprivileged NEAR caller can invoke this function with a crafted `tx_id` that is actually an internal Merkle tree node hash, causing the function to return `true` for a transaction that was never included in the block. The secure replacement, `verify_transaction_inclusion_v2`, adds the missing coinbase proof check, but the deprecated path remains fully reachable.

---

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated(since = "0.5.0")]` in the contract, with the note explicitly stating the reason: it lacks coinbase Merkle proof validation needed to mitigate the 64-byte transaction Merkle proof forgery vulnerability. [1](#0-0) 

Despite the deprecation, the function remains a live, `#[pause]`-gated public entry point with no additional access restriction. Any NEAR account can call it: [2](#0-1) 

The function's verification logic computes only the transaction's own Merkle path against the block's `merkle_root`: [3](#0-2) 

It performs **no validation** that `args.tx_id` corresponds to a real leaf transaction rather than an internal Merkle tree node. The contract's own warning documents this gap: [4](#0-3) 

By contrast, `verify_transaction_inclusion_v2` adds the coinbase proof check that closes this gap: [5](#0-4) 

The 64-byte transaction attack (https://www.bitmex.com/blog/64-Byte-Transactions) works as follows: because Bitcoin's Merkle tree is built by hashing pairs of 32-byte child hashes (producing a 64-byte input), an attacker can craft a fake "transaction" whose hash equals an internal node hash. When this internal node hash is supplied as `tx_id` with a corresponding proof path, `compute_root_from_merkle_proof` will reconstruct the correct `merkle_root`, and the function returns `true` — falsely confirming inclusion of a non-existent transaction.

---

### Impact Explanation

Any NEAR smart contract that calls `verify_transaction_inclusion` to gate a financial action (e.g., releasing funds upon Bitcoin payment confirmation) can be deceived into accepting a forged SPV proof. The attacker does not need any privileged role; they only need to construct a valid-looking `ProofArgs` with a crafted `tx_id` (an internal node hash) and a matching Merkle path. The corrupted output is a `true` return value for a transaction that was never mined.

**Impact: High** — forged transaction inclusion proofs can unlock funds or trigger irreversible on-chain actions in consuming contracts.

---

### Likelihood Explanation

The entry path is fully open to any unprivileged NEAR caller. The 64-byte transaction attack is publicly documented and well-understood. The attacker needs only to identify a real Bitcoin block, extract an internal Merkle node hash, and construct the corresponding proof path — all computable from public blockchain data. The deprecated function is not hidden or restricted.

**Likelihood: Medium** — requires off-chain computation to construct the forged proof, but no privileged access whatsoever.

---

### Recommendation

Remove the callable body of `verify_transaction_inclusion` entirely, or replace its implementation with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")` to force all callers onto the safe path. Retaining a deprecated but fully functional insecure entry point is the direct analog of keeping `latestAnswer` in production: the safer replacement exists, but the broken path remains reachable.

---

### Proof of Concept

1. Identify any Bitcoin block `B` with known `merkle_root`.
2. From `B`'s transaction list, compute the Merkle tree and extract an internal node hash `N` at depth `d`.
3. Construct a `ProofArgs` with:
   - `tx_id = N` (the internal node hash, not a real txid)
   - `tx_block_blockhash` = hash of block `B` (already stored in the contract)
   - `tx_index` = the leaf index that `N` would occupy if treated as a leaf
   - `merkle_proof` = the sibling path from `N` up to `merkle_root`
   - `confirmations = 1`
4. Call `verify_transaction_inclusion(args)` on the NEAR contract.
5. The call returns `true` because `compute_root_from_merkle_proof(N, index, proof) == merkle_root`, even though no transaction with hash `N` exists in block `B`. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L277-280)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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

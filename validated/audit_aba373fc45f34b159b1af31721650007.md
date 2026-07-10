### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Validation — (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` is still a live, publicly callable NEAR entry point despite being deprecated in favour of `verify_transaction_inclusion_v2`. Any unprivileged NEAR caller can invoke the old function directly, completely bypassing the coinbase Merkle proof check that was introduced specifically to block the 64-byte transaction Merkle-proof forgery attack. This is a direct structural analog to the reported bug: the protocol added a "safe" code path but left the unsafe one reachable, so an attacker simply uses the unsafe path.

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the well-known 64-byte transaction vulnerability (documented in the code's own comments and the BitMEX reference). It does so by first verifying a coinbase Merkle proof before delegating to the old function: [1](#0-0) 

The old function, however, is still `pub` and decorated with `#[pause]` (not `#[private]`), making it a first-class NEAR contract method callable by any account: [2](#0-1) 

The function's own warning acknowledges the risk: [3](#0-2) 

The coinbase proof check that `verify_transaction_inclusion_v2` performs before calling the old function is the only guard against the forgery: [4](#0-3) 

Because `verify_transaction_inclusion` is still directly callable, an attacker skips that guard entirely.

### Impact Explanation

The 64-byte attack works as follows: Bitcoin's Merkle tree hashes pairs of 32-byte child hashes into a 64-byte input. An internal node's 64-byte preimage can be interpreted as two concatenated 32-byte "transaction IDs." An attacker who finds (or crafts) such a node can supply its hash as `tx_id` and a valid Merkle path to the root, causing `compute_root_from_merkle_proof` to return the real block's `merkle_root` for a transaction that does not exist. [5](#0-4) 

Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate fund releases, cross-chain swaps, or other state transitions will accept a forged Bitcoin transaction proof, enabling theft or double-spend without any real Bitcoin on-chain activity.

### Likelihood Explanation

The entry point is public and requires no privileged role. The attacker only needs to:
1. Identify a real mainchain block stored in the contract.
2. Construct a 64-byte internal-node preimage that parses as a plausible `tx_id`.
3. Call `verify_transaction_inclusion` directly (not `_v2`) with the forged arguments.

No key compromise, social engineering, or special configuration is required. The attack is executable by any NEAR account.

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or add `#[private]` to prevent external calls. Alternatively, have `verify_transaction_inclusion` internally delegate to `verify_transaction_inclusion_v2` so the coinbase check is always enforced, regardless of which entry point is used. Downstream integrators should be notified to migrate to `verify_transaction_inclusion_v2` immediately.

### Proof of Concept

1. Pick any block hash `B` stored in `mainchain_header_to_height`.
2. Retrieve the block's `merkle_root` from `headers_pool`.
3. Construct a fake `tx_id = H` where `H` is an internal Merkle node such that `compute_root_from_merkle_proof(H, idx, proof)` equals the real `merkle_root`.
4. Call `verify_transaction_inclusion` (not `_v2`) with `tx_block_blockhash = B`, `tx_id = H`, `tx_index = idx`, `merkle_proof = proof`, `confirmations = 1`.
5. The function returns `true` for a transaction that was never broadcast on Bitcoin, because the coinbase proof guard present only in `verify_transaction_inclusion_v2` was never executed. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
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

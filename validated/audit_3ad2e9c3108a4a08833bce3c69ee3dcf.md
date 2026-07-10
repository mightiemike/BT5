### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Validation — (`contract/src/lib.rs`)

### Summary
The contract exposes two code paths for transaction inclusion verification: `verify_transaction_inclusion` (v1) and `verify_transaction_inclusion_v2` (v2). Only v2 validates the coinbase Merkle proof required to prevent 64-byte transaction forgery. Because v1 is still a live public NEAR contract method, any unprivileged caller can invoke it directly, bypassing the coinbase proof check entirely and causing the contract to return `true` for a fraudulent transaction inclusion claim.

### Finding Description
`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability. It enforces two additional checks before delegating to v1:

1. Proof length parity: `merkle_proof.len() == coinbase_merkle_proof.len()`
2. Coinbase Merkle proof validation against the stored block's `merkle_root` [1](#0-0) 

The original `verify_transaction_inclusion` performs none of these checks. It computes a Merkle root from the caller-supplied `tx_id` and `merkle_proof` and compares it directly to the stored `merkle_root`, with no coinbase anchor: [2](#0-1) 

The contract's own warning acknowledges this: *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."* [3](#0-2) 

Despite being marked `#[deprecated]`, the function carries `pub` visibility and the `#[pause]` attribute — meaning it remains a fully callable NEAR contract entry point. Rust's `#[deprecated]` attribute emits only a compiler warning; it does not restrict on-chain invocation. [4](#0-3) 

This is a direct structural analog to the reported `_checkSlippage` bug: two code paths exist for the same operation, one of which omits a critical range/validity check, and the unsafe path remains reachable by an unprivileged caller.

### Impact Explanation
An attacker who knows the Merkle tree structure of any confirmed Bitcoin block can craft a `tx_id` equal to an internal Merkle node and supply a corresponding `merkle_proof` that reconstructs the correct `merkle_root`. Calling `verify_transaction_inclusion` with this forged proof returns `true`. Any downstream NEAR contract that gates fund releases, bridge withdrawals, or state transitions on this return value will be deceived into accepting a Bitcoin transaction that never existed. The corrupted value is the proof result (`bool`) returned to the consuming contract.

### Likelihood Explanation
The 64-byte transaction attack is well-documented and mechanically straightforward. The attacker needs only a confirmed block's Merkle tree (publicly available from any Bitcoin node) and the ability to call a NEAR contract method — no privileged role, no key material, no social engineering. The entry point is `verify_transaction_inclusion`, callable by any NEAR account.

### Recommendation
Remove `verify_transaction_inclusion` as a callable public entry point. The simplest fix is to change its visibility from `pub` to `pub(crate)` or to remove the `#[near]`/`#[pause]` exposure entirely, forcing all callers to use `verify_transaction_inclusion_v2`. Alternatively, inline the coinbase proof check directly into v1 so both paths apply the same unified validation, eliminating the asymmetry.

### Proof of Concept

1. Obtain the Merkle tree for any confirmed Bitcoin block (e.g., block at height `H` already stored in the contract's mainchain).
2. Select any internal Merkle node hash `N` at depth `d`. Compute the sibling path from `N` up to the root — this is the forged `merkle_proof`.
3. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the hash of block `H`
   - `tx_index` = the leaf-position implied by the sibling path
   - `merkle_proof` = the sibling path from `N` to the root
   - `confirmations = 1`
4. `compute_root_from_merkle_proof(N, tx_index, merkle_proof)` reconstructs the correct `merkle_root` stored in the block header.
5. The function returns `true` — confirming inclusion of a transaction that does not exist. [5](#0-4) [6](#0-5)

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

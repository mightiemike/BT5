### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery - (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` (v1) is still a live, unpermissioned public entry point on the contract. It accepts any 32-byte hash as `tx_id` without validating that it is a Merkle leaf (an actual transaction). An unprivileged NEAR caller can supply an internal Merkle tree node hash as `tx_id` with a shortened proof path and receive a `true` return value for a transaction that does not exist.

### Finding Description
The contract exposes two SPV proof verification functions. `verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle forgery vulnerability by requiring a coinbase proof that anchors the tree depth. However, the original `verify_transaction_inclusion` (v1) was only marked `#[deprecated]` — it was not removed, not access-controlled, and not gated behind any role check. It remains callable by any NEAR account. [1](#0-0) 

The function's own documentation acknowledges the broken invariant: [2](#0-1) 

The verification logic in `compute_root_from_merkle_proof` operates purely on the supplied `tx_id`, `tx_index`, and `merkle_proof` without any constraint that `tx_id` must be a leaf node: [3](#0-2) 

For a block whose Merkle tree has leaves `[T0, T1, T2, T3]`, the internal node `N = H(T0, T1)` is a valid 32-byte hash. An attacker submits `tx_id = N`, `tx_index = 0`, `merkle_proof = [H(T2, T3)]`. `compute_root_from_merkle_proof` computes `H(N, H(T2,T3)) = root`, which equals the block's stored `merkle_root`, so the function returns `true`. [4](#0-3) 

### Impact Explanation
Any downstream contract or application that calls `verify_transaction_inclusion` (v1) to gate a financial action (e.g., releasing bridged assets, crediting a deposit, unlocking collateral) will accept a forged proof as valid. The attacker does not need to mine a block or control any privileged role — they only need to know the Merkle tree structure of any confirmed Bitcoin block already tracked by the light client, which is public information.

### Likelihood Explanation
The entry point is public and requires no stake, role, or special permission. The Merkle tree structure of every Bitcoin block is publicly derivable from block explorers. The attack requires only off-chain computation to identify a usable internal node and construct the shortened proof. Any integrator that has not independently audited the deprecation warning and migrated to v2 is exposed.

### Recommendation
Remove `verify_transaction_inclusion` (v1) from the contract's public interface entirely, or add an `#[private]` attribute so it is no longer externally callable. The v2 function already provides the correct mitigation via coinbase proof anchoring. [5](#0-4) 

### Proof of Concept

1. Identify any Bitcoin block already accepted into the light client's mainchain (e.g., block at height `H` with known `blockhash`).
2. Retrieve the block's full transaction list `[T0, T1, ..., Tn]` from a public block explorer.
3. Compute the Merkle tree. Pick any internal node `N` at depth `d` (e.g., `N = H(T0, T1)` at depth 1).
4. Construct a proof of length `tree_depth - d` that walks from `N` up to the root.
5. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash — not a real transaction)
   - `tx_block_blockhash = blockhash`
   - `tx_index = 0` (position of `N` in its level)
   - `merkle_proof = [sibling of N, ..., up to root]`
   - `confirmations = 1`
6. The function returns `true`, falsely attesting that `N` is a confirmed transaction in that block. [6](#0-5)

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

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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

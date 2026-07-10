### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Guard — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` (v1) is still a live, unprivileged public entry point despite being deprecated. It lacks the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` enforces. Any NEAR caller can invoke v1 directly, bypassing the guard that prevents the 64-byte internal-node Merkle proof forgery attack, and receive a `true` inclusion result for a transaction that was never in the block.

### Finding Description
`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). It does so by requiring a coinbase Merkle proof that anchors the tree depth before delegating to v1: [1](#0-0) 

However, v1 is still decorated only with `#[deprecated]` and `#[pause]` — neither of which restricts runtime access by an unprivileged caller. `#[deprecated]` is a Rust compiler hint; it emits no on-chain enforcement. The function remains a fully reachable public method: [2](#0-1) 

The code itself documents the broken invariant:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

`compute_root_from_merkle_proof` in `merkle-tools` is a pure hash-chain computation with no depth or coinbase anchor check: [4](#0-3) 

An attacker supplies a 64-byte internal Merkle tree node as `tx_id` together with a valid sibling path. The hash chain resolves to the block's `merkle_root`, so the function returns `true` for a fabricated transaction.

### Impact Explanation
Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate a financial or state-changing action (e.g., cross-chain bridge unlock, payment settlement) will accept a forged proof. The attacker does not need to mine a block or control any privileged role — only a valid block already stored in the contract's `headers_pool` and knowledge of its Merkle tree structure are required. The corrupted result is a `true` return value for a transaction that does not exist.

### Likelihood Explanation
The entry point is public and requires no special role. Bitcoin Merkle trees and their internal nodes are fully public data. The 64-byte forgery technique is well-documented and has known tooling. Any relayer-path user or external contract consumer can trigger this with a single cross-contract call.

### Recommendation
Remove the `pub` visibility from `verify_transaction_inclusion` or gate it with `#[private]` so it can only be called internally (as a helper for v2). Alternatively, delete the function body and have it unconditionally panic with a migration message. The coinbase-proof guard in v2 must be the only externally reachable code path.

### Proof of Concept
1. Identify any block stored in `headers_pool` with a known Merkle tree (e.g., from a public Bitcoin block explorer).
2. Select any internal Merkle tree node `N` at depth ≥ 1 (a 32-byte hash that is not a leaf transaction).
3. Construct a valid sibling path from `N` to the block's `merkle_root`.
4. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_block_blockhash = <known block>`, `tx_index = <position of N>`, `merkle_proof = <sibling path>`, `confirmations = 1`.
5. The function returns `true` because `compute_root_from_merkle_proof(N, position, proof) == merkle_root` holds by construction, yet `N` is not a valid transaction hash. [5](#0-4)

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

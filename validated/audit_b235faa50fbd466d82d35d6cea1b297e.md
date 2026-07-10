### Title
Deprecated `verify_transaction_inclusion` Remains Callable, Bypassing Coinbase Merkle Proof Validation — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` is still a live, publicly callable NEAR contract method despite being deprecated. It omits the coinbase Merkle proof check that was introduced in `verify_transaction_inclusion_v2` to mitigate the 64-byte transaction Merkle proof forgery attack. Any unprivileged caller can invoke the deprecated path directly, bypassing the critical validation and obtaining a `true` result for a forged transaction inclusion proof.

### Finding Description
`verify_transaction_inclusion_v2` was introduced to fix the 64-byte transaction Merkle proof forgery vulnerability (documented at https://www.bitmex.com/blog/64-Byte-Transactions). The fix works by requiring the caller to also supply a coinbase Merkle proof, which is verified against the block's `merkle_root` at position 0 before the main proof is checked. [1](#0-0) 

However, the original `verify_transaction_inclusion` function is still a public, on-chain callable method. It carries only a Rust `#[deprecated]` attribute, which is a compile-time warning with no effect on the deployed WASM ABI. The function remains fully reachable by any NEAR account. [2](#0-1) 

The function's own documentation acknowledges the flaw explicitly:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

The only check performed is that `merkle_proof` is non-empty and that the computed root matches the stored `merkle_root`: [4](#0-3) 

No coinbase proof is required. An attacker can supply an internal Merkle tree node as `tx_id` together with a valid sibling path, and the function will return `true`.

### Impact Explanation
Any downstream contract or off-chain system that calls `verify_transaction_inclusion` to gate a cross-chain action (e.g., releasing funds, minting tokens, confirming a bridge deposit) can be deceived into accepting a forged proof. The attacker does not need to mine a Bitcoin block; they only need to know the internal node hashes of any real block already stored in the contract's `headers_pool`. The contract returns `true` for a `tx_id` that is not a real transaction, corrupting the proof-verification result that downstream consumers rely on.

### Likelihood Explanation
The entry path is fully unprivileged: any NEAR account can call `verify_transaction_inclusion` directly. The required inputs (a real block hash in the mainchain, its Merkle tree structure) are publicly available from any Bitcoin block explorer. The attack requires no special role, no leaked key, and no social engineering. The only prerequisite is that the target block is within the contract's `gc_threshold` window.

### Recommendation
Remove `verify_transaction_inclusion` from the public ABI entirely. Because `#[deprecated]` has no runtime effect in NEAR/Rust WASM, the function must be made `pub(crate)` or deleted. If backward compatibility is required for a transition period, gate the function behind an access-control role (e.g., `Role::DAO`) so unprivileged callers cannot reach it. All callers should be migrated to `verify_transaction_inclusion_v2`.

### Proof of Concept

1. Identify any Bitcoin block `B` stored in the contract's mainchain (e.g., via `get_block_hash_by_height`).
2. Obtain the full transaction list of `B` from a public Bitcoin node.
3. Compute the internal Merkle node `N = H(T0 || T1)` (the parent of the first two transactions).
4. Construct a valid Merkle proof for `N` at index 0 with sibling `H(T2 || T3)` (and further siblings up to the root). This proof is valid because `compute_root_from_merkle_proof(N, 0, [H(T2,T3), ...])` equals the real `merkle_root` stored in the block header.
5. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_block_blockhash = B.hash`, `tx_index = 0`, `merkle_proof = [H(T2,T3), ...]`, `confirmations = 1`.
6. The contract returns `true` for a transaction that does not exist. [5](#0-4) 

The `compute_root_from_merkle_proof` function is purely positional — it has no awareness of whether the leaf is a real transaction or an internal node — so the forged proof passes the equality check at line 318–322 of `lib.rs`. [6](#0-5)

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

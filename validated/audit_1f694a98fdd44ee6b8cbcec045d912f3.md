### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Coinbase Proof Guard, Enabling 64-Byte Transaction Forgery — (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (v1) is still reachable by any unprivileged NEAR caller. It lacks the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` introduced to close the 64-byte transaction Merkle forgery path. An attacker can supply a crafted `tx_id` that is an internal Merkle tree node hash, receive a `true` return value, and deceive any consuming contract into treating a non-existent Bitcoin transaction as confirmed.

### Finding Description

`verify_transaction_inclusion` computes the Merkle root from the caller-supplied proof and compares it to the stored `merkle_root` in the block header. It performs no check that `tx_id` is a leaf node (a real transaction hash) rather than an internal Merkle tree node. [1](#0-0) 

`verify_transaction_inclusion_v2` was introduced specifically to close this gap. It first validates a coinbase Merkle proof that anchors the proof path to the coinbase transaction at index 0 — a known-valid leaf — before delegating to v1. [2](#0-1) 

However, v1 carries only `#[pause]` (no role restriction beyond the global pause flag) and the Rust `#[deprecated]` attribute, which generates a compiler warning but does not prevent runtime invocation. Any NEAR account can call v1 directly, bypassing the coinbase proof check entirely. [3](#0-2) 

**Broken invariant:** `verify_transaction_inclusion` must return `true` only when `tx_id` is a real transaction hash confirmed in the block at `tx_block_blockhash`. This invariant is violated because `compute_root_from_merkle_proof` treats `tx_id` as an opaque 32-byte value and will correctly reconstruct the stored `merkle_root` even when `tx_id` is an internal Merkle node hash, not a leaf. [4](#0-3) 

The v1 function's own documentation acknowledges this: [5](#0-4) 

### Impact Explanation

Any NEAR contract or user that calls `verify_transaction_inclusion` (v1) to gate cross-chain actions — releasing bridged funds, minting tokens, confirming payments — can be deceived by a forged proof. The attacker receives `true` for a transaction that was never broadcast or confirmed on Bitcoin. Because the verification result is the sole trust anchor for cross-chain transaction validity in this light-client design, a false positive directly enables theft of bridged assets or unauthorized state changes in consuming contracts. Impact is **high**.

### Likelihood Explanation

The 64-byte transaction forgery attack is well-documented (cited in the v2 docstring itself). The v1 function is publicly callable with no role restriction. Any NEAR account can call it without staking, registration, or privileged access. The only prerequisite is constructing a valid Merkle proof path where the supplied `tx_id` is an internal node of a real, stored block — computationally feasible given public Bitcoin block data. Likelihood is **medium-high**.

### Recommendation

Remove external callability of v1. The simplest fix is to change its visibility from `pub` to `pub(crate)`, since `verify_transaction_inclusion_v2` already calls it internally:

```rust
// Before
pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }

// After
pub(crate) fn verify_transaction_inclusion(&self, ...) -> bool { ... }
```

Alternatively, add a role restriction so only trusted callers (e.g., `Role::DAO`) can invoke v1, or remove v1 entirely and require all callers to migrate to v2.

### Proof of Concept

1. Attacker identifies a real Bitcoin block `B` with `merkle_root = R`, already stored in the contract's `headers_pool`.
2. Attacker inspects `B`'s full transaction list and locates any internal Merkle node `N` at depth `d` (e.g., the hash of the first two transaction hashes).
3. Attacker constructs a Merkle proof `P` consisting of the sibling hashes from `N` up to the root, such that `compute_root_from_merkle_proof(N, index_of_N_at_depth_d, P) == R`.
4. Attacker calls `verify_transaction_inclusion` with:
   - `tx_id = N` (internal node hash, not a real transaction)
   - `tx_block_blockhash = B`
   - `tx_index` and `merkle_proof = P` as constructed above
   - `confirmations` within the stored `gc_threshold`
5. The function passes all `require!` guards and returns `true`.
6. Any consuming contract that trusts this result releases funds or performs actions as if the fake transaction was confirmed on Bitcoin. [6](#0-5) [4](#0-3)

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

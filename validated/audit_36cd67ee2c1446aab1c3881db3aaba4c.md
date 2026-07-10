### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Merkle Forgery Protection — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is still a live, unpermissioned public entry point on the contract. The coinbase Merkle proof validation that guards against the 64-byte transaction Merkle forgery attack is present only in `verify_transaction_inclusion_v2`. Any unprivileged NEAR caller can invoke the deprecated function directly, bypassing that protection entirely and obtaining a `true` SPV result for a non-existent transaction.

---

### Finding Description

The contract exposes two public SPV verification entry points:

**`verify_transaction_inclusion`** (deprecated since 0.5.0): [1](#0-0) 

It carries an explicit `# Warning` in its own doc comment acknowledging the forgery risk, but it remains `pub` with only a `#[pause]` guard (not paused by default). It performs no coinbase proof check.

**`verify_transaction_inclusion_v2`** (current): [2](#0-1) 

This function adds the coinbase Merkle proof check that anchors the tree depth and prevents the 64-byte internal-node forgery. It then delegates to the deprecated function for the rest of the checks.

The `#[deprecated]` Rust attribute is a **compiler warning only**. It imposes no runtime restriction. Any NEAR account can call `verify_transaction_inclusion` directly as a contract method, skipping `verify_transaction_inclusion_v2` and its coinbase validation entirely.

The `ProofArgs` struct accepted by the deprecated function contains no `coinbase_tx_id` or `coinbase_merkle_proof` fields: [3](#0-2) 

So the caller is structurally unable to supply coinbase data through this path — the protection is architecturally absent, not merely skipped.

The underlying Merkle computation in `merkle-tools` has no internal safeguard against 64-byte inputs: [4](#0-3) 

---

### Impact Explanation

The 64-byte transaction Merkle forgery (https://www.bitmex.com/blog/64-Byte-Transactions) works as follows: a Bitcoin Merkle internal node is exactly 64 bytes (two concatenated 32-byte child hashes). An attacker can craft a fake `tx_id` that equals an internal node of a real block's Merkle tree, then supply a shorter Merkle proof path that terminates at a higher level of the tree. `compute_root_from_merkle_proof` will compute the correct Merkle root from this forged input, causing `verify_transaction_inclusion` to return `true` for a transaction that does not exist in the block.

Any bridge, atomic swap, or cross-chain lending protocol that calls `verify_transaction_inclusion` to confirm a Bitcoin payment will accept a forged proof as valid. This enables an attacker to claim a Bitcoin transfer occurred when it did not, draining funds from the consumer contract.

---

### Likelihood Explanation

The entry point is unconditionally reachable by any NEAR account with no role, stake, or deposit requirement beyond the standard NEAR gas fee. The forgery technique is publicly documented and has known tooling. The only prerequisite is identifying a suitable real mainchain block (already stored in the contract) and computing the internal-node collision offline — no on-chain mining or privileged access is required.

---

### Recommendation

Remove `verify_transaction_inclusion` as a callable public method. Because `#[deprecated]` provides no runtime enforcement, the function must either be deleted or have its visibility reduced to `pub(crate)` / replaced with a hard `env::panic_str` body. `verify_transaction_inclusion_v2` should be the sole externally callable SPV verification entry point.

---

### Proof of Concept

1. Identify any mainchain block `B` stored in the contract with at least two transactions (so the Merkle tree has at least one internal node).
2. Obtain the Merkle tree of `B`. Pick any internal node `N` at depth `d` from the root. `N` is 64 bytes — the concatenation of its two child hashes.
3. Treat `N` as a `tx_id` (it is a valid `H256` after truncation/interpretation). Construct a Merkle proof of length `(tree_depth - d)` that walks from `N` up to the root using real sibling hashes.
4. Call `verify_transaction_inclusion` directly (not `_v2`) with:
   - `tx_id = N`
   - `tx_block_blockhash = B`
   - `tx_index` = the index corresponding to `N`'s position at depth `d`
   - `merkle_proof` = the shortened proof
   - `confirmations = 1`
5. `compute_root_from_merkle_proof` reconstructs the correct Merkle root from the forged inputs.
6. The function returns `true` — confirming inclusion of a transaction that does not exist.

The coinbase check in `verify_transaction_inclusion_v2` would have rejected this call because the coinbase proof length would not match the forged proof length, but that check is entirely absent in the deprecated path. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L263-323)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
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

**File:** btc-types/src/contract_args.rs (L16-25)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
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

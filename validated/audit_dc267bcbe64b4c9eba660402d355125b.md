### Title
Deprecated `verify_transaction_inclusion` Permanently Lacks Coinbase Proof Check, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is a publicly callable NEAR function that accepts an attacker-supplied `tx_id` and a Merkle proof, then returns `true` if the computed root matches the stored block header's `merkle_root`. It performs no check that `tx_id` is a leaf node (a real transaction) rather than an internal Merkle tree node. The `#[deprecated]` Rust attribute is a compile-time hint only — the function remains fully callable on-chain by any unprivileged NEAR account. Any consumer contract that calls v1 can be deceived into accepting a fabricated transaction inclusion proof, leading to direct loss of funds.

---

### Finding Description

The contract stores a `skip_pow_verification` flag and exposes two SPV proof functions. The v2 function (`verify_transaction_inclusion_v2`) mitigates the known 64-byte Merkle forgery attack by first validating a coinbase proof: [1](#0-0) 

The v1 function omits this check entirely. It accepts any 32-byte value as `tx_id`, computes `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof)`, and returns `true` if the result equals the stored `merkle_root`: [2](#0-1) 

The only guard is `require!(!args.merkle_proof.is_empty())`, which blocks a zero-step proof but does not prevent the attack when the proof has ≥ 1 element.

`compute_root_from_merkle_proof` in `merkle-tools` is a pure hash-chain computation with no leaf-vs-internal-node distinction: [3](#0-2) 

The `#[deprecated]` attribute on v1 generates a Rust compiler warning for Rust callers only. It does not remove the function from the deployed WASM binary or prevent on-chain invocation: [4](#0-3) 

The function is also `#[pause]`-able but is not paused by default, so it is live on deployment.

---

### Impact Explanation

An attacker can forge a proof that a non-existent Bitcoin transaction was included in a real, confirmed block. Any consumer contract (bridge, atomic swap, cross-chain lending protocol) that calls `verify_transaction_inclusion` v1 and releases funds upon a `true` result will be deceived. The impact is **direct loss of funds** from those consumer contracts — identical in class to the target report's impact.

---

### Likelihood Explanation

The attack requires:
1. A real confirmed Bitcoin block already tracked by the contract (no privileged access needed).
2. Knowledge of the block's Merkle tree structure (publicly available from any Bitcoin node).
3. A single NEAR transaction calling `verify_transaction_inclusion` with a crafted `tx_id` equal to an internal Merkle node.

No staking, no role, no private key is required. The function is callable by any NEAR account. Consumer contracts that integrated before v2 was introduced, or that did not update their call target, remain permanently exposed.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the contract entirely, or add a `require!(false, "use verify_transaction_inclusion_v2")` guard to make it unconditionally revert. A Rust `#[deprecated]` attribute is insufficient — it does not prevent on-chain calls. All downstream integrators must be migrated to `verify_transaction_inclusion_v2` before v1 is removed.

---

### Proof of Concept

Given a real Bitcoin block `B` with Merkle tree:

```
        root
       /    \
     n01     n23
    /   \   /   \
  tx0  tx1 tx2  tx3
```

1. Compute `n01 = double_sha256(tx0 || tx1)` — this is a known internal node.
2. Call `verify_transaction_inclusion` with:
   - `tx_id = n01` (internal node, not a real transaction)
   - `tx_block_blockhash` = hash of block `B` (already in the contract's canonical chain)
   - `tx_index = 0`
   - `merkle_proof = [n23]` (one-element proof)
   - `confirmations = 1`
3. The contract computes `compute_root_from_merkle_proof(n01, 0, [n23])` = `double_sha256(n01 || n23)` = `root` = `B.merkle_root`.
4. The function returns `true`.
5. A bridge contract observing this result releases funds for a Bitcoin deposit that never occurred. [5](#0-4) [6](#0-5)

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

**File:** merkle-tools/src/lib.rs (L54-60)
```rust
fn compute_hash(first_tx_hash: &H256, second_tx_hash: &H256) -> H256 {
    let mut concat_inputs = Vec::with_capacity(64);
    concat_inputs.extend(first_tx_hash.0);
    concat_inputs.extend(second_tx_hash.0);

    double_sha256(&concat_inputs)
}
```

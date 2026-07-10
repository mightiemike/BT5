### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable and Lacks Coinbase Merkle Proof Guard, Enabling 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR contract method. It performs no coinbase Merkle proof validation, leaving it fully exposed to the well-known 64-byte transaction Merkle proof forgery attack. Any unprivileged NEAR caller can invoke it directly, bypassing the mitigation that was added in `verify_transaction_inclusion_v2`, and obtain a `true` return value for a transaction that was never included in any Bitcoin block.

---

### Finding Description

The contract exposes two transaction-inclusion verification methods:

**v2 (the safe path)** — `verify_transaction_inclusion_v2` requires the caller to also supply a coinbase Merkle proof at index 0 with the same depth as the target proof. This anchors the proof depth to the real tree depth and prevents an internal Merkle node from being presented as a leaf transaction. [1](#0-0) 

**v1 (the vulnerable path)** — `verify_transaction_inclusion` is annotated `#[deprecated]` in Rust source, but that annotation is a compile-time hint for Rust callers only. It has zero effect on external NEAR callers who invoke the method through the NEAR RPC interface. The function remains a fully public, pausable contract method with no coinbase guard: [2](#0-1) 

The only validation performed is that `merkle_proof` is non-empty and that the computed root matches the stored `merkle_root`: [3](#0-2) 

`compute_root_from_merkle_proof` in `merkle-tools` is a straightforward iterative hash-and-climb that accepts any `(tx_id, tx_index, proof)` triple without any constraint on proof depth relative to the actual tree: [4](#0-3) 

**The 64-byte forgery attack path:**

Bitcoin Merkle internal nodes are produced by `double_sha256(left_32_bytes || right_32_bytes)` — a 64-byte input. A 64-byte serialized Bitcoin transaction is also valid. An attacker who controls or observes a real block can:

1. Identify an internal Merkle node `N` at depth `d` in the tree.
2. Interpret the two 32-byte children of `N` as a fake 64-byte "transaction" `T_fake`.
3. Construct a Merkle proof for `T_fake` that is one step shorter than a leaf-level proof (depth `d-1` instead of `d`).
4. Call `verify_transaction_inclusion` with `tx_id = T_fake`, `tx_index` matching the position of `N`, and the shortened proof.
5. `compute_root_from_merkle_proof` climbs the tree from `N` and correctly reaches `merkle_root` — the contract returns `true`.

`T_fake` was never a real transaction in the block.

---

### Impact Explanation

Any downstream NEAR contract or application that calls `verify_transaction_inclusion` (v1) to gate a security-critical action — e.g., releasing bridged funds, confirming a cross-chain payment, or proving BTC deposit — will accept a forged proof as valid. The attacker can fabricate evidence of a Bitcoin transaction that does not exist, enabling theft or unauthorized state transitions in consuming contracts.

This matches the external report's impact class: **proof-verification forgery** leading to an inability to enforce the security invariant (here: "only real, included transactions pass verification").

---

### Likelihood Explanation

- The entry point is fully permissionless: any NEAR account can call `verify_transaction_inclusion` with arbitrary `ProofArgs`.
- The attack requires only a real, confirmed Bitcoin block (publicly available) and knowledge of its Merkle tree structure (also public).
- No privileged role, private key, or social engineering is needed.
- The `#[deprecated]` annotation provides no on-chain protection whatsoever.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public contract interface entirely, or add the same coinbase proof guard that `verify_transaction_inclusion_v2` uses. Keeping a deprecated but callable method that is documented as vulnerable to a known forgery attack is equivalent to leaving the vulnerability open. [5](#0-4) 

---

### Proof of Concept

Given a real Bitcoin block with transactions `[T0_coinbase, T1, T2, T3]` and Merkle tree:

```
         Root
        /    \
      N01    N23
      / \    / \
    T0  T1  T2  T3
```

Internal node `N01 = double_sha256(T0 || T1)`.

Attacker constructs `T_fake` as the 64-byte concatenation `T0 || T1` (interpreted as a raw transaction whose txid is `N01`).

Call to `verify_transaction_inclusion`:
- `tx_id` = `N01`
- `tx_index` = 0 (left child of root)
- `merkle_proof` = `[N23]` (one element, depth 1)
- `tx_block_blockhash` = hash of the real block
- `confirmations` = 1

`compute_root_from_merkle_proof(N01, 0, [N23])`:
- position 0 is even → `hash(N01, N23)` = `Root` ✓

Contract returns `true`. `T_fake` was never a real transaction. [6](#0-5) [7](#0-6)

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

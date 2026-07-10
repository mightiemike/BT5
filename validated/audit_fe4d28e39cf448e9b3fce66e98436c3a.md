### Title
Deprecated `verify_transaction_inclusion` remains publicly callable, allowing any caller to bypass the coinbase Merkle proof validation enforced by `verify_transaction_inclusion_v2` — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle-proof forgery vulnerability by requiring a coinbase Merkle proof. However, the original `verify_transaction_inclusion` (v1) remains a live, unguarded public entry point. Any unprivileged NEAR caller can invoke v1 directly, skipping the coinbase check entirely, and obtain a `true` result for a forged transaction inclusion claim.

---

### Finding Description

`verify_transaction_inclusion_v2` enforces a coinbase Merkle proof before delegating to v1: [1](#0-0) 

The coinbase check at lines 358–365 is the only guard against the 64-byte internal-node forgery. After it passes, v2 calls v1 via `self.verify_transaction_inclusion(args.into())`.

v1 itself performs no such check: [2](#0-1) 

v1 only verifies that `tx_block_blockhash` is in `mainchain_header_to_height` and that the supplied Merkle path reconstructs the block's `merkle_root`. It carries an explicit warning that it may return `true` for an internal-node hash, and the `#[deprecated]` Rust attribute is a compile-time hint only — it imposes no runtime restriction. The function is decorated with `#[pause]` but is otherwise fully reachable by any NEAR account when the contract is not paused.

The inconsistency mirrors the ERC20Boost pattern exactly:

| | ERC20Boost analog | BTC light client |
|---|---|---|
| "Safe" path | `decrementGaugesBoostIndexed` — forces full removal for deprecated gauge | `verify_transaction_inclusion_v2` — requires coinbase proof |
| "Unsafe" path | `decrementGaugeBoost` — allows partial decrement on deprecated gauge | `verify_transaction_inclusion` — no coinbase proof |
| Bypass | Call the unsafe path directly | Call v1 directly |

---

### Impact Explanation

An attacker who controls a Bitcoin block's Merkle tree (or who can observe one) can craft a 64-byte value whose double-SHA256 hash equals an internal Merkle-tree node. By supplying this value as `tx_id` together with a valid sibling path, `compute_root_from_merkle_proof` will reconstruct the correct `merkle_root`, and v1 returns `true` for a transaction that does not exist.

Any consumer NEAR contract that calls `verify_transaction_inclusion` (v1) — whether by mistake, by referencing an older ABI, or because v2 is unavailable — will accept forged SPV proofs. This corrupts the core security guarantee of the light client: that a `true` result means the transaction is genuinely included in a confirmed Bitcoin block. [3](#0-2) 

---

### Likelihood Explanation

The 64-byte transaction forgery attack is publicly documented (referenced in the contract's own NatSpec at line 268). The entry point is reachable by any unprivileged NEAR account with no staking, role, or deposit requirement beyond the normal call fee. A consumer contract that was written against the v1 ABI, or that simply calls the wrong method name, is silently vulnerable. The `#[deprecated]` marker does not prevent on-chain invocation. [4](#0-3) 

---

### Recommendation

Remove the `#[pause]` guard from v1 and replace the function body with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")`, or delete the public method entirely and keep only the private helper logic that v2 calls internally. Alternatively, add the same coinbase-proof check to v1 so both entry points are equally safe, eliminating the inconsistency.

---

### Proof of Concept

1. A block at height H is confirmed on the main chain with `merkle_root = R`.
2. The attacker identifies two adjacent leaf hashes `L1 ∥ L2` (64 bytes) whose `SHA256d` equals an internal node `N` that lies on the Merkle path to `R`.
3. The attacker calls `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node, presented as a "transaction hash")
   - `tx_block_blockhash` = the confirmed block hash
   - `merkle_proof` = the sibling path from `N` up to `R`
   - `tx_index` = the index corresponding to `N`'s position
4. `compute_root_from_merkle_proof(N, index, proof)` reconstructs `R` correctly.
5. v1 returns `true` — a forged SPV proof accepted by the contract.
6. Had the attacker called `verify_transaction_inclusion_v2`, step 3 would require a valid coinbase proof at index 0, which the attacker cannot produce for an arbitrary internal node, and the call would panic at line 364. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L263-270)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
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

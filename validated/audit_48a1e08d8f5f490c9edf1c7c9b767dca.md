### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Merkle Proof Forgery Mitigation Added in v2 — (`File: contract/src/lib.rs`)

---

### Summary

The contract exposes two separate entry points for SPV proof verification: the deprecated `verify_transaction_inclusion` (v1) and the current `verify_transaction_inclusion_v2` (v2). Version 2 was introduced specifically to mitigate the 64-byte transaction Merkle second-preimage attack by requiring an additional coinbase proof. However, v1 remains publicly callable by any unprivileged NEAR account with no access control beyond `#[pause]`. An attacker can bypass v2's protection entirely by calling v1 directly, forging a transaction inclusion proof against any real mainchain block, and causing any downstream bridge or cross-chain contract to release funds for a non-existent Bitcoin transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` was added to mitigate the known 64-byte transaction Merkle forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions). Its implementation adds a coinbase proof check before delegating to v1: [1](#0-0) 

The coinbase proof anchors the tree depth, preventing an attacker from presenting an internal node as a leaf transaction. However, v1 is still a live, public contract method: [2](#0-1) 

It carries only a Rust `#[deprecated]` attribute and a `#[pause]` guard — neither of which prevents an unprivileged NEAR caller from invoking it on-chain: [3](#0-2) 

The core proof check in v1 is: [4](#0-3) 

No coinbase proof, no tree-depth validation, no caller restriction. The `#[trusted_relayer]` macro that guards `submit_blocks` is entirely absent here. [5](#0-4) 

The two-implementation structure is:

```
verify_transaction_inclusion_v2  ──► coinbase_check + verify_transaction_inclusion (v1)
verify_transaction_inclusion (v1) ──► raw merkle check only  ← STILL REACHABLE DIRECTLY
```

This is structurally identical to the reported Shardus bug: a fix applied to one route leaves the other route independently vulnerable, and fixing one does not fix the other.

---

### Impact Explanation

The 64-byte second-preimage attack works as follows against v1:

1. Take any real mainchain block `B` with ≥2 transactions (i.e., any non-coinbase-only block).
2. Extract the merkle tree. An internal node `N` at level `k` is `SHA256d(left_child || right_child)` where both children are 32 bytes. The concatenation `left_child || right_child` is 64 bytes.
3. Treat this 64-byte value as a "transaction" `T_fake`. Construct a merkle proof from `N`'s position up to the root.
4. Call `verify_transaction_inclusion` with `tx_id = T_fake`, `tx_block_blockhash = B.hash`, `tx_index = position_of_N`, `merkle_proof = path_from_N_to_root`.
5. The function computes `compute_root_from_merkle_proof(T_fake, ...)` and obtains the real merkle root → returns `true`.

Any bridge or cross-chain contract that calls v1 to gate fund releases will be deceived into releasing funds for a Bitcoin transaction that does not exist. This is a direct loss of funds.

---

### Likelihood Explanation

- The attacker is any unprivileged NEAR account — no role, stake, or key is required.
- Every real Bitcoin mainchain block with more than one transaction is a valid target; this covers essentially the entire chain.
- The attack requires only public block data (merkle tree structure) and a single NEAR contract call.
- The `verify_transaction_inclusion` method is explicitly listed in the public ABI and is callable via the NEAR RPC view interface.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or add `#[private]` / `#[trusted_relayer]` to make it uncallable by external accounts. The internal call from `verify_transaction_inclusion_v2` can be refactored into a private helper function so the coinbase-check path is the only externally reachable entry point. [6](#0-5) 

---

### Proof of Concept

1. Deploy the contract (Bitcoin build) with a real mainchain genesis and submit several real headers so the chain has blocks with multiple transactions.
2. Pick any mainchain block `B` with ≥2 transactions. Extract its merkle tree from the raw block data.
3. Select any internal node `N` at level 1: `N = SHA256d(T0 || T1)` where `T0`, `T1` are the first two transaction IDs.
4. Construct `T_fake = T0 || T1` (64 bytes, treated as a transaction hash by passing its 32-byte SHA256d as `tx_id`).
5. Call (as any unprivileged account, no deposit required since it is a view):

```
verify_transaction_inclusion({
  tx_id: SHA256d(T_fake),   // the internal node hash N
  tx_block_blockhash: B.hash,
  tx_index: 0,              // position of N in the level-1 subtree
  merkle_proof: [...],      // siblings from N up to root
  confirmations: 0
})
```

6. The function returns `true` for a transaction that does not exist in the Bitcoin blockchain.
7. A bridge contract gating fund releases on this result would release funds to the attacker. [4](#0-3) [1](#0-0)

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
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

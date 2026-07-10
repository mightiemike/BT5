### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Merkle Proof Forgery Check — (File: `contract/src/lib.rs`)

---

### Summary

The coinbase Merkle proof check — introduced to prevent the 64-byte transaction Merkle proof forgery attack — was placed exclusively in `verify_transaction_inclusion_v2`. The original `verify_transaction_inclusion` (v1) remains a live, publicly callable NEAR entry point with no access restriction beyond the `#[pause]` gate. Any unprivileged NEAR caller can invoke v1 directly, bypassing the coinbase proof check entirely, and receive a `true` return value for a forged transaction inclusion proof.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle proof forgery vulnerability documented at https://www.bitmex.com/blog/64-Byte-Transactions. It does so by requiring the caller to supply a coinbase Merkle proof and verifying it against the block's `merkle_root` before delegating to v1:

```rust
// contract/src/lib.rs  lines 347-369
pub fn verify_transaction_inclusion_v2(&self, args: ProofArgsV2) -> bool {
    require!(
        args.merkle_proof.len() == args.coinbase_merkle_proof.len(), ...
    );
    let header = self.headers_pool.get(&args.tx_block_blockhash)...;
    require!(
        merkle_tools::compute_root_from_merkle_proof(
            args.coinbase_tx_id.clone(), 0usize, &args.coinbase_merkle_proof,
        ) == header.block_header.merkle_root,
        "Incorrect coinbase merkle proof"
    );
    #[allow(deprecated)]
    self.verify_transaction_inclusion(args.into())   // ← delegates to v1
}
```

The original v1 function, however, is still decorated with `#[pause]` (not `#[private]`) and remains a fully public NEAR method:

```rust
// contract/src/lib.rs  lines 283-323
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, args: ProofArgs) -> bool {
    // ... confirmations check, mainchain membership check ...
    merkle_tools::compute_root_from_merkle_proof(
        args.tx_id,
        usize::try_from(args.tx_index).unwrap(),
        &args.merkle_proof,
    ) == header.block_header.merkle_root
    // ← NO coinbase proof check
}
```

`#[deprecated]` in Rust is a compile-time lint for Rust callers only. It has zero effect on the NEAR ABI: the method is exported and callable by any external account. The coinbase proof check therefore exists only in the v2 code path and is completely absent from the v1 code path.

This is structurally identical to the reported pattern: a critical check was placed in one function (`verify_transaction_inclusion_v2`) while a parallel entry point (`verify_transaction_inclusion`) bypasses it entirely.

---

### Impact Explanation

**Impact: High**

A successful exploit causes the contract to return `true` for a proof that a Bitcoin transaction was included in a block when no such transaction exists. Any downstream NEAR contract that calls `verify_transaction_inclusion` and acts on a `true` result (e.g., releasing funds, minting tokens, unlocking a bridge transfer) will be deceived into treating a forged Bitcoin event as real.

The corrupted value is the **proof result** (`bool`) returned by the contract's verification API — the primary security guarantee of the light client.

---

### Likelihood Explanation

**Likelihood: High**

- The entry point is unconditionally reachable by any NEAR account (no role, stake, or whitelist required beyond the contract not being paused).
- The 64-byte transaction Merkle forgery technique is publicly documented and has known tooling.
- The attacker only needs to identify a mainchain block, craft a 64-byte payload whose double-SHA256 collides with an internal Merkle node, and supply a valid Merkle path — all off-chain work with no on-chain cost beyond the NEAR gas fee.
- Downstream integrators may call v1 because it has a simpler argument structure (`ProofArgs` vs `ProofArgsV2`) or because they copied an older integration example.

---

### Recommendation

Remove the `#[pause]` attribute from `verify_transaction_inclusion` and replace it with `#[private]`, or add `env::panic_str("use verify_transaction_inclusion_v2")` as the first statement, so the method is no longer callable by external accounts. Alternatively, delete the method entirely if no on-chain callers depend on it.

---

### Proof of Concept

1. Identify any block hash `B` present in `mainchain_header_to_height` (e.g., via `get_block_hash_by_height`).
2. Retrieve the block's `merkle_root` from `headers_pool`.
3. Construct a 64-byte payload `P` such that `SHA256d(P)` equals an internal Merkle tree node reachable from `merkle_root` with a short proof path (standard 64-byte Merkle forgery technique).
4. Set `tx_id = SHA256d(P)`, `tx_index` to the corresponding leaf position, and `merkle_proof` to the path from that internal node to `merkle_root`.
5. Call `verify_transaction_inclusion` with `tx_block_blockhash = B`, `confirmations = 1`, and the crafted proof.
6. The contract returns `true` for a transaction that does not exist in the Bitcoin block.

The call to `verify_transaction_inclusion_v2` with the same `tx_id` and no valid `coinbase_tx_id` would fail at the coinbase proof `require!`, confirming that the protection exists only in the v2 path. [1](#0-0) [2](#0-1)

### Citations

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

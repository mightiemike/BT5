### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Forgery Protection — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced specifically to add a coinbase Merkle proof check that mitigates the well-known 64-byte transaction Merkle proof forgery attack. However, the original `verify_transaction_inclusion` function it wraps remains a `pub fn` on the NEAR contract, callable by any unprivileged account. Any caller can invoke the deprecated function directly, bypassing the coinbase proof validation entirely and obtaining a `true` SPV verification result for a transaction that was never included in the block.

---

### Finding Description

`verify_transaction_inclusion_v2` performs a coinbase Merkle proof check before delegating to `verify_transaction_inclusion`: [1](#0-0) 

The coinbase check (lines 358–365) is the sole mitigation for the 64-byte Merkle proof forgery vulnerability. After passing it, the function calls `self.verify_transaction_inclusion(args.into())`.

The inner function is declared as: [2](#0-1) 

`#[deprecated]` in Rust is a **compile-time lint**, not a runtime access restriction. On NEAR Protocol, every `pub fn` in a `#[near]` impl block is automatically exposed as a callable contract method. The deprecation attribute has zero effect on external callers — any NEAR account can call `verify_transaction_inclusion` directly, receiving a `bool` result with no coinbase proof validation applied.

The contract's own documentation acknowledges the danger: [3](#0-2) 

---

### Impact Explanation

The 64-byte Merkle proof forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to craft a 64-byte value that is simultaneously a valid internal Merkle tree node and a plausible "transaction hash." By calling `verify_transaction_inclusion` directly with such a crafted `tx_id` and a matching `merkle_proof`, the attacker can make the contract return `true` for a Bitcoin transaction that was never broadcast or confirmed. Any downstream NEAR contract that gates an action (e.g., minting tokens, releasing funds, confirming a cross-chain payment) on a `true` result from this SPV endpoint is deceived into accepting a forged proof. The corrupted value is the SPV proof result itself — the canonical invariant that a `true` return means the transaction genuinely exists in a confirmed Bitcoin block.

---

### Likelihood Explanation

The entry path requires no privileges: any NEAR account can call `verify_transaction_inclusion` as a view call with attacker-controlled `ProofArgs`. The 64-byte forgery technique is publicly documented and has known tooling. The only prerequisite is that a valid block header has been submitted to the contract (which is the normal operating state). Likelihood is high wherever a downstream contract consumes the SPV result without independently enforcing use of `_v2`.

---

### Recommendation

- **Short term**: Remove the `pub` visibility from `verify_transaction_inclusion` or gate it with an explicit `#[private]` attribute so it is no longer callable as an external NEAR contract method. Alternatively, add the coinbase proof check directly inside `verify_transaction_inclusion` so both entry points are equally safe.
- **Long term**: Audit all public contract methods to ensure that deprecated security-sensitive functions cannot be called externally. Treat `#[deprecated]` as documentation only, never as an access control mechanism on NEAR.

---

### Proof of Concept

1. A valid Bitcoin block header is submitted to the contract via `submit_blocks` (normal relayer operation).
2. The attacker identifies the block's `merkle_root` from on-chain state.
3. The attacker constructs a 64-byte value `fake_tx` such that `double_sha256(fake_tx)` equals the stored `merkle_root` (the standard 64-byte Merkle forgery technique).
4. The attacker calls `verify_transaction_inclusion` directly (not `_v2`) with:
   - `tx_id = fake_tx`
   - `tx_block_blockhash` = the target block hash
   - `tx_index = 0`
   - `merkle_proof = []` (empty, since `fake_tx` hashes directly to the root)
5. `compute_root_from_merkle_proof(fake_tx, 0, &[])` returns `double_sha256(fake_tx)` == `merkle_root`.
6. The function returns `true`.
7. Any downstream contract checking `verify_transaction_inclusion(...) == true` accepts the forged proof as a confirmed Bitcoin transaction.

The coinbase check in `verify_transaction_inclusion_v2` (lines 358–365) would have rejected this at step 4, but it is never reached because the attacker bypasses `_v2` entirely. [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L346-368)
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
```

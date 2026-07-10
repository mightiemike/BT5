### Title
Deprecated `verify_transaction_inclusion` Remains Fully Callable On-Chain, Bypassing v2 Coinbase Proof Validation — (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes two public SPV verification endpoints: `verify_transaction_inclusion` (v1, deprecated) and `verify_transaction_inclusion_v2`. v2 was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase Merkle proof. However, v1 carries only a Rust `#[deprecated]` attribute, which is a compile-time lint with zero enforcement at the NEAR protocol level. Any unprivileged NEAR account can call v1 directly, bypassing v2's coinbase proof check entirely, and obtain a `true` SPV result for a transaction that was never included in a Bitcoin block.

---

### Finding Description

`verify_transaction_inclusion` (v1) is decorated with `#[deprecated(since = "0.5.0", note = "Use verify_transaction_inclusion_v2 instead.")]` and `#[pause]`. [1](#0-0) 

`#[deprecated]` is a Rust compiler attribute. It emits a warning to Rust callers at compile time. It does **not** remove the method from the compiled WASM binary, does not restrict who can call it via NEAR RPC, and does not prevent cross-contract calls. The method is a live, unrestricted on-chain endpoint.

`verify_transaction_inclusion_v2` adds a mandatory coinbase Merkle proof check before delegating to v1: [2](#0-1) 

The coinbase check at lines 358–365 is the only guard against the 64-byte transaction Merkle proof forgery attack. v1 has no equivalent check: [3](#0-2) 

The CLAUDE.md explicitly documents the v1 vulnerability: "This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash." [4](#0-3) 

The result is two live, publicly callable paths to the same SPV verification result — one with the coinbase security check (v2) and one without (v1) — directly analogous to the external report's dual-path permission structure.

---

### Impact Explanation

An unprivileged attacker can call `verify_transaction_inclusion` (v1) with a crafted internal Merkle tree node hash as `tx_id` and receive `true` — a forged on-chain certification that a non-existent Bitcoin transaction was included in a confirmed block. Any downstream NEAR contract that consumes this result (e.g., a bridge contract releasing assets upon SPV proof) will be deceived into treating a fabricated transaction as confirmed. This is a proof-verification forgery with direct asset-theft potential in any bridge or settlement contract that relies on this light client.

---

### Likelihood Explanation

The attack requires only public Bitcoin blockchain data (block headers and Merkle trees are fully public) and the ability to call a NEAR contract method — no privileged keys, no special roles, no leaked secrets. The internal Merkle node hashes needed to forge a proof are computable from any Bitcoin block explorer. The v1 endpoint is callable by any NEAR account via RPC `view` call or `call` transaction. Likelihood is high wherever a downstream contract calls v1 instead of v2, or wherever an attacker can influence which endpoint a downstream contract queries.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the on-chain ABI entirely. Since v2 already delegates to v1 internally after the coinbase check, the internal logic can be preserved as a private helper. Alternatively, add an explicit `env::panic_str("use verify_transaction_inclusion_v2")` guard at the top of v1 to make it unconditionally revert when called externally, while keeping the internal call path from v2 via a separate private method. The `#[deprecated]` attribute alone provides no on-chain protection.

---

### Proof of Concept

Given a real Bitcoin block `B` stored in the contract's `headers_pool` with Merkle root `R` and two transactions `[T0, T1]`:

1. The internal Merkle node at depth 1 is `N = SHA256d(T0 || T1)`, which equals `R` for a two-transaction block.
2. Attacker calls `verify_transaction_inclusion` with:
   - `tx_id = T0` (a real leaf — but at a depth that makes it an internal node in a crafted proof)
   - `tx_block_blockhash = B`
   - `tx_index = 0`
   - `merkle_proof = [T1]` (sibling)
3. `compute_root_from_merkle_proof(T0, 0, [T1])` returns `SHA256d(T0 || T1) = R`.
4. The function returns `true`.

Now the attacker substitutes a **fabricated** `tx_id` — any 32-byte value `F` — and finds or constructs a `merkle_proof` path such that `compute_root_from_merkle_proof(F, idx, proof) == R`. Because the contract stores no transaction count and cannot validate proof depth, any internal node of the Merkle tree can be presented as a leaf. The v1 endpoint returns `true` for `F`, which was never a real transaction.

Calling v2 with the same `F` would fail at line 358–364 because the coinbase proof check would not reconstruct `R` from a fabricated coinbase hash. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L317-323)
```rust
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

**File:** contract/CLAUDE.md (L64-66)
```markdown
`verify_transaction_inclusion(ProofArgs)` — SPV proof: given a tx hash, block hash, and merkle proof, verifies the transaction is in the block by recomputing the merkle root.

**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
```

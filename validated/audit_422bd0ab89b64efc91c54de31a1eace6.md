### Title
`verify_transaction_inclusion_v2` Silently Broken When `verify_transaction_inclusion` Is Independently Paused — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` carries its own `#[pause]` guard but internally delegates its final proof check to `self.verify_transaction_inclusion(...)`, which carries a **separate, independent** `#[pause]` guard. A `PauseManager` who pauses only the deprecated v1 function — a natural operational step when deprecating an API — unknowingly also breaks v2 for every unprivileged caller, with no on-chain retry or recovery path.

---

### Finding Description

`verify_transaction_inclusion_v2` is the current, recommended proof-verification entry point. It adds a coinbase Merkle proof check to mitigate the 64-byte transaction forgery vulnerability, then delegates the core SPV check to the deprecated v1:

```rust
// contract/src/lib.rs  line 346-369
#[pause]
pub fn verify_transaction_inclusion_v2(&self, args: ProofArgsV2) -> bool {
    // ... coinbase proof check ...
    #[allow(deprecated)]
    self.verify_transaction_inclusion(args.into())   // ← internal call to v1
}
``` [1](#0-0) 

The deprecated v1 carries its own independent `#[pause]` decorator:

```rust
// contract/src/lib.rs  line 283-288
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, args: ProofArgs) -> bool { ... }
``` [2](#0-1) 

The `near_plugins` `#[pause]` macro (v0.2.0, tag v0.4.1) injects a pause-state check at the top of the function body. Because `verify_transaction_inclusion_v2` calls `self.verify_transaction_inclusion(...)` as a regular Rust method call on `self`, the injected check inside v1 executes unconditionally — it does not distinguish between an external NEAR call and an internal Rust call. Pausing v1 therefore also panics every call that flows through v2. [3](#0-2) 

There is no `claimable` mapping, no pending-request queue, and no retry mechanism in the contract. A downstream dApp that has already committed to a cross-chain action (e.g., locked BTC on the source chain, initiated a bridge transfer) and now calls `verify_transaction_inclusion_v2` will receive a panic with no on-chain path to recover the verification result.

---

### Impact Explanation

Any downstream consumer contract that calls `verify_transaction_inclusion_v2` will panic and its transaction will be rolled back while v1 is paused. If the consumer has already committed an irreversible action on another chain (the canonical use-case for an SPV light client), the cross-chain state becomes permanently desynchronized until the pause is manually lifted. The contract exposes no retry or claim mechanism analogous to `OmniGasPump.owed`.

---

### Likelihood Explanation

The deprecation notice on v1 explicitly instructs users to migrate to v2. A `PauseManager` following standard deprecation hygiene — pausing the old endpoint to force migration — will naturally pause v1 while leaving v2 active, believing v2 remains fully functional. The hidden internal dependency is not documented, not enforced by the type system, and not guarded by any ordering check. This is a realistic, low-sophistication operational mistake.

---

### Recommendation

Remove the `#[pause]` decorator from `verify_transaction_inclusion` (v1) and instead gate it only through the v2 pause flag, **or** inline the v1 logic directly into v2 so there is a single pause surface. If v1 must remain independently pausable for external callers, add an explicit guard inside `verify_transaction_inclusion_v2` that checks the v1 pause state before delegating and surfaces a clear error, or restructure v2 to not call v1 at all. Document the dependency prominently so operators know that pausing v1 also disables v2.

---

### Proof of Concept

1. `PauseManager` calls `pa_pause_feature("verify_transaction_inclusion")` to deprecate the v1 endpoint.
2. An unprivileged NEAR account (e.g., a bridge contract) calls `verify_transaction_inclusion_v2` with a valid `ProofArgsV2`.
3. v2's own pause check passes (v2 is not paused).
4. v2 executes its coinbase Merkle check — passes.
5. v2 calls `self.verify_transaction_inclusion(args.into())`.
6. The `#[pause]`-injected check inside v1 fires: `pa_is_paused("verify_transaction_inclusion")` → `true` → `env::panic_str(...)`.
7. The entire transaction is rolled back. The downstream bridge contract's cross-chain state is now inconsistent with no on-chain recovery path. [4](#0-3)

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

**File:** contract/Cargo.toml (L44-44)
```text
near-plugins = { git = "https://github.com/aurora-is-near/near-plugins", tag = "v0.4.1" }
```

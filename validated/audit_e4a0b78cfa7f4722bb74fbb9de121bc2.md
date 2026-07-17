### Title
`promise_batch_action_transfer_to_gas_key` and `promise_batch_action_add_gas_key_with_full_access` host functions charge exec fee using raw borsh wire length instead of trie key length for ML-DSA-65 keys — (File: `runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

The `promise_batch_action_transfer_to_gas_key` and `promise_batch_action_add_gas_key_with_full_access` host functions compute the per-byte exec fee using `public_key_len` — the raw borsh wire length supplied by the calling contract. For ML-DSA-65 keys this is 1953 bytes. The static `exec_fee` path in `runtime/runtime/src/config.rs` correctly uses `action.public_key.trie_id_len()` — 33 bytes for ML-DSA-65 — because the key is stored in the trie as a SHA3-256 hash, not as the full pubkey. The two paths diverge by 1920 bytes, causing the host-function path to overcharge the exec fee by approximately 33 Tgas per `TransferToGasKey` call and a proportional amount per nonce for `AddGasKey`.

---

### Finding Description

**Correct static path** (`runtime/runtime/src/config.rs`):

```rust
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
``` [1](#0-0) 

`trie_id_len()` returns 33 for `PublicKey::MLDSA65` (1 tag byte + 32-byte SHA3-256 digest):

```rust
pub fn trie_id_len(&self) -> usize {
    match self {
        Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
        Self::SECP256K1(_) => 1 + 64,
        Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,   // 33
    }
}
``` [2](#0-1) 

**Divergent host-function path** (`runtime/near-vm-runner/src/logic/logic.rs`):

```rust
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← raw wire length from contract memory, not trie_id_len()
);
``` [3](#0-2) 

Identical divergence in the wasmtime runner: [4](#0-3) 

`gas_key_transfer_exec_fee` feeds `public_key_len` directly into `access_key_key_len`, which computes the trie key length:

```rust
pub fn gas_key_transfer_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,          // should be trie_id_len, not wire len
) -> GasKeyTransferFee {
    let trie_key_len = access_key_key_len(account_id_len, public_key_len);
    ...
    let per_byte = cfg.fee(ActionCosts::gas_key_byte).exec_fee()
        .checked_mul((trie_key_len + estimated_value_len) as u64).unwrap();
    ...
}
``` [5](#0-4) 

The same divergence exists in `promise_batch_action_add_gas_key_with_full_access`, where `gas_key_add_key_exec_fee` is called with `public_key_len as usize` instead of `trie_id_len()`: [6](#0-5) 

versus the static path: [7](#0-6) 

The design documentation explicitly flags this invariant:

> "`PublicKey::trie_id_len()` is a new contract that all storage-cost code must respect. Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes." [8](#0-7) 

The host-function paths were not updated to honour this contract.

---

### Impact Explanation

For an ML-DSA-65 key the divergence is 1953 − 33 = **1920 bytes**.

- `gas_key_byte.exec_fee` = 17,212,011 gas/byte (mainnet parameters snapshot).
- Extra exec gas per `TransferToGasKey` call: 1920 × 17,212,011 ≈ **33 Tgas**.
- For `AddGasKey` with `num_nonces` nonces the overcharge scales as 1920 × `gas_key_byte.exec_fee` × `num_nonces`.

A contract that calls `promise_batch_action_transfer_to_gas_key` with an ML-DSA-65 key is charged ~33 Tgas more than the equivalent `TransferToGasKey` transaction action. This breaks the fee-domain invariant that the same action costs the same regardless of how it is submitted, and can cause contracts to exhaust their gas budget unexpectedly. The same action priced at two different values also breaks deterministic receipt-cost accounting.

---

### Likelihood Explanation

Requires `ProtocolFeature::PostQuantumSignatures` to be active (ML-DSA-65 keys cannot be added before that gate). Once active, any contract that manages ML-DSA-65 gas keys and calls `promise_batch_action_transfer_to_gas_key` or `promise_batch_action_add_gas_key_with_full_access` triggers the overcharge. The calling contract controls `public_key_len` directly from its own memory, so no privileged role is required.

---

### Recommendation

In both `promise_batch_action_transfer_to_gas_key` (logic.rs and wasmtime_runner/logic.rs), after decoding the public key, pass `public_key.trie_id_len()` to `gas_key_transfer_exec_fee` instead of `public_key_len as usize`:

```rust
let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
// ...
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key.trie_id_len(),   // ← use trie id length, not wire length
);
```

Apply the same fix to `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call`, passing `public_key.trie_id_len()` to `gas_key_add_key_exec_fee`. The send fee correctly continues to use the wire length (what the sender transmits).

---

### Proof of Concept

1. `PostQuantumSignatures` is enabled on the network.
2. Account `alice` holds an ML-DSA-65 gas key.
3. A contract calls `promise_batch_action_transfer_to_gas_key` targeting `alice`'s ML-DSA-65 key (borsh-encoded length = 1953 bytes).
4. The host function charges exec fee based on `access_key_key_len(alice_id.len(), 1953)`.
5. The equivalent `Action::TransferToGasKey` submitted as a signed transaction charges exec fee based on `access_key_key_len(alice_id.len(), 33)` (via `action.public_key.trie_id_len()`).
6. The difference in `gas_key_byte.exec_fee` charged is `(1953 − 33) × 17,212,011 ≈ 33 Tgas` — the contract path is overcharged relative to the transaction path for the identical on-chain operation.

### Citations

**File:** runtime/runtime/src/config.rs (L347-350)
```rust
        TransferToGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
```

**File:** runtime/runtime/src/config.rs (L389-394)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
    );
```

**File:** core/crypto/src/signature.rs (L333-339)
```rust
    pub fn trie_id_len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,
        }
    }
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3091-3096)
```rust
        let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
        let exec = gas_key_transfer_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
        );
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3325)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3395-3400)
```rust
    let exec_fee = gas_key_add_key_exec_fee(
        &ctx.fees_config,
        receiver_id.len(),
        public_key_len as usize,
        num_nonces,
    );
```

**File:** core/parameters/src/cost.rs (L833-846)
```rust
pub fn gas_key_transfer_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).exec_fee();
    let trie_key_len = access_key_key_len(account_id_len, public_key_len);
    let estimated_value_len = AccessKey::min_gas_key_borsh_len();
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .exec_fee()
        .checked_mul((trie_key_len + estimated_value_len) as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
```

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```

### Title
ML-DSA-65 Gas-Key Host Functions Use Wire-Format Key Length Instead of Trie-ID Length for Fee Calculation — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

### Summary

The `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` host functions compute gas fees using the raw borsh wire-format length of the caller-supplied public key (`public_key_len as usize` = 1953 bytes for ML-DSA-65) instead of the on-trie identifier length (`trie_id_len()` = 33 bytes). The transaction-action path in `runtime/runtime/src/config.rs` correctly uses `trie_id_len()` for the same fee helpers. The host-function path was never updated, creating a ~59× overcharge on the per-byte fee component for any contract that funds or creates an ML-DSA-65 gas key.

### Finding Description

When the `PostQuantumSignatures` protocol feature is active, ML-DSA-65 access keys are stored in the trie by their 32-byte SHA3-256 hash (33 bytes including the type tag), not by the 1952-byte raw public key. `PublicKey::trie_id_len()` returns 33 for ML-DSA-65 and `PublicKey::len()` returns 1953.

The documentation and codebase explicitly state that every storage-stake and trie-byte-priced fee path must call `trie_id_len()`:

> "Every storage-stake and trie-byte-priced fee path was updated to call `trie_id_len()`."
> "Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes."

The **transaction path** in `total_send_fees` and `exec_fee` correctly uses `trie_id_len()`:

```rust
// runtime/runtime/src/config.rs:114-116
TransferToGasKey(action) => {
    gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
        .total()
}
// runtime/runtime/src/config.rs:347-350
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
```

The **host-function path** in both the wasmer and wasmtime runners passes the raw `public_key_len` argument (the borsh wire length supplied by the contract) directly to the same fee helpers:

```rust
// runtime/near-vm-runner/src/logic/logic.rs:3091-3095
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,  // ← wire length, not trie_id_len
);
```

```rust
// runtime/near-vm-runner/src/wasmtime_runner/logic.rs:3323-3325
let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
let exec =
    gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

Inside `gas_key_transfer_exec_fee`, `public_key_len` is fed directly into `access_key_key_len`, which computes the trie key length:

```rust
// core/parameters/src/cost.rs:839
let trie_key_len = access_key_key_len(account_id_len, public_key_len);
```

For ML-DSA-65, `public_key_len = 1953` inflates `trie_key_len` by 1920 bytes relative to the correct value of 33. The same error propagates through `gas_key_add_key_exec_fee` called from `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call`.

The exact divergent values:
- Wire length passed: `1 + 1952 = 1953` bytes
- Correct trie-id length: `1 + 32 = 33` bytes
- Overcharge per call: `1920 × gas_key_byte_fee` gas units on the exec component

### Impact Explanation

Any contract that calls `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, or `promise_batch_action_add_gas_key_with_function_call` with an ML-DSA-65 public key is overcharged approximately 59× on the per-byte gas component compared to the equivalent transaction-action path. The overcharge is burnt gas (real NEAR token loss). The discrepancy also breaks fee parity between the transaction and host-function paths for the same logical operation, violating the invariant tested in `test_gas_key_fee_parity`.

### Likelihood Explanation

The `PostQuantumSignatures` feature is present in the codebase and gated by protocol version. Once activated, any contract that manages ML-DSA-65 gas keys via host functions will trigger the overcharge on every call. The caller controls the public key type and can supply a valid ML-DSA-65 key. No privileged role is required.

### Recommendation

In both `runtime/near-vm-runner/src/logic/logic.rs` and `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`, after decoding the public key from guest memory, replace `public_key_len as usize` with `public_key.trie_id_len()` (or `public_key.decode()?.trie_id_len()`) when calling `gas_key_transfer_send_fee`, `gas_key_transfer_exec_fee`, and `gas_key_add_key_exec_fee`. This mirrors the transaction path in `runtime/runtime/src/config.rs` which already calls `action.public_key.trie_id_len()`.

### Proof of Concept

For an ML-DSA-65 gas key on an account with a 10-byte account ID:

**Transaction path** (`total_send_fees` / `exec_fee`):
- `trie_id_len = 33`
- `trie_key_len = access_key_key_len(10, 33)` → correct trie key length

**Host-function path** (`promise_batch_action_transfer_to_gas_key`):
- `public_key_len = 1953` (borsh wire length of ML-DSA-65 pubkey)
- `trie_key_len = access_key_key_len(10, 1953)` → inflated by 1920 bytes
- Exec per-byte overcharge: `1920 × gas_key_byte_exec_fee`

The existing test `test_gas_key_fee_parity` asserts `fund_a_outcome.gas_burnt == fund_b_outcome.gas_burnt` (transaction vs host function), but it only uses `KeyType::ED25519` keys where `len() == trie_id_len()`. Adding an ML-DSA-65 variant to that test would expose the divergence. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** runtime/runtime/src/config.rs (L114-116)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
```

**File:** runtime/runtime/src/config.rs (L347-350)
```rust
        TransferToGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
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

**File:** core/crypto/src/signature.rs (L326-339)
```rust
    /// Length, in bytes, of the on-trie identifier for an access-key
    /// entry owned by this public key. For ed25519/secp256k1 this matches
    /// `len()`; for ML-DSA-65 the trie stores a SHA3-256 hash (33 bytes
    /// including the type tag), not the 1953-byte borsh-encoded pubkey.
    /// Used by storage-fee calculations on the runtime side; cheap to call
    /// (no hashing) - for ML-DSA-65 this returns the size of the digest
    /// form without actually hashing the pubkey.
    pub fn trie_id_len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,
        }
    }
```

**File:** docs/architecture/how/post_quantum_signatures.md (L126-138)
```markdown
### 5. Storage usage and fee plumbing

The storage-stake calculation
(`runtime/runtime/src/access_keys.rs::access_key_storage_usage`) and the
gas-key fee helpers (`gas_key_*_fee` in `runtime/runtime/src/config.rs`) use
`PublicKey::trie_id_len()` rather than `PublicKey::len()`:

- `len()` reports the borsh-encoded length (33 / 65 / 1953 across the
  three `PublicKey` variants).
- `trie_id_len()` reports the on-trie length (33 / 65 / **33**).

The two diverge only for `PublicKey::MLDSA65`. Every storage-stake and
trie-byte-priced fee path was updated to call `trie_id_len()`.
```

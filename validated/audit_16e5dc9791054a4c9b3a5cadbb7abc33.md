### Title
ML-DSA-65 gas-key host functions pass wire-encoded key length to exec-fee helpers instead of trie-id length, breaking fee-domain parity with the transaction path — (`runtime/near-vm-runner/src/logic/logic.rs`)

---

### Summary

The three gas-key host functions (`promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`) forward the raw `public_key_len` argument — the borsh wire length of the key as supplied by the calling contract — directly into `gas_key_transfer_exec_fee` and `gas_key_add_key_exec_fee`. For ML-DSA-65 keys that wire length is **1953 bytes** (1 tag + 1952 raw pubkey). The actual trie key for an ML-DSA-65 access key uses the SHA3-256 hash form: **33 bytes** (1 tag + 32 digest). The transaction path that computes the same exec fee (`permission_exec_fees` in `runtime/runtime/src/config.rs`) correctly calls `public_key.trie_id_len()` = 33. The divergence is **1920 bytes** per nonce, multiplied by `gas_key_byte` exec fee and by `num_nonces`. The existing fee-parity test (`test_gas_key_fee_parity`) only exercises ED25519 keys, where `len()` and `trie_id_len()` are identical, so the divergence is undetected.

---

### Finding Description

`PublicKey::trie_id_len()` was introduced precisely because ML-DSA-65 access keys are stored in the trie as a 33-byte SHA3-256 digest, not as the 1953-byte borsh-encoded full pubkey. Every storage-stake and trie-byte-priced fee path in the **transaction** runtime was updated to call `trie_id_len()`. The **host-function** path was not.

**Transaction path** (`runtime/runtime/src/config.rs`, `permission_exec_fees`):
```rust
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // 33 for ML-DSA-65
    gas_key_info.num_nonces,
);
```

**Host-function path** (`runtime/near-vm-runner/src/logic/logic.rs`, `promise_batch_action_add_gas_key_with_full_access`):
```rust
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // 1953 for ML-DSA-65 (wire length)
    num_nonces,
);
```

`gas_key_add_key_exec_fee` feeds `public_key_len` directly into `access_key_key_len`, which adds it to the trie-key length estimate:

```rust
let nonce_key_len =
    access_key_key_len(account_id_len, public_key_len) + size_of::<NonceIndex>();
```

For ML-DSA-65 the nonce-key length is computed as `2 + account_id_len + 1953 + 2` instead of the correct `2 + account_id_len + 33 + 2`, an overcount of **1920 bytes per nonce**.

The same pattern applies to `gas_key_transfer_exec_fee` called from `promise_batch_action_transfer_to_gas_key`:

```rust
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // 1953 for ML-DSA-65
);
```

The identical bug is present in the wasmtime backend (`runtime/near-vm-runner/src/wasmtime_runner/logic.rs`).

---

### Impact Explanation

**Fee-domain invariant broken**: the exec gas charged for adding or funding an ML-DSA-65 gas key via a contract host function is ~1920 × `gas_key_byte_exec_fee` × `num_nonces` higher than the identical action submitted as a direct transaction. With `gas_key_byte` exec fee at its current calibration and `num_nonces` up to 65535 (u16::MAX), the overcharge can reach tens of teragas per call, causing contracts that add ML-DSA-65 gas keys programmatically to exhaust their gas budget unexpectedly. Because the overcharge is deterministic and applied identically on all nodes, it does not cause consensus failure, but it silently misprices every ML-DSA-65 gas-key host-function call relative to the protocol's stated fee model and relative to the transaction path — exactly the "wrong unit substituted for the correct one" pattern of the seed report.

---

### Likelihood Explanation

`PostQuantumSignatures` and `GasKeys` both stabilized at protocol version 85 (current stable). Any contract that programmatically manages ML-DSA-65 gas keys — a natural use case once PQ keys are in production — will trigger the overcharge on every `AddKey` or `TransferToGasKey` host-function call. The calling contract supplies `public_key_len` from its own memory, so the divergence is fully user-controlled and requires no privileged role.

---

### Recommendation

In `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, and `promise_batch_action_transfer_to_gas_key` (both `logic.rs` and `wasmtime_runner/logic.rs`), decode the public key first and pass `public_key.trie_id_len()` to the exec-fee helpers instead of the raw `public_key_len`:

```rust
// After: let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
let decoded_pk = public_key.decode()?;
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    decoded_pk.trie_id_len(),   // 33 for ML-DSA-65, 33 for ED25519, 65 for SECP256K1
    num_nonces,
);
```

Extend `test_gas_key_fee_parity` to cover `KeyType::MLDSA65` so the invariant is enforced for all key types.

---

### Proof of Concept

**Exact divergent values** (ML-DSA-65, `num_nonces = 4`, `account_id = "alice.near"` = 10 bytes):

| Path | `public_key_len` passed | `nonce_key_len` | overcharge per nonce |
|------|------------------------|-----------------|----------------------|
| Transaction (`permission_exec_fees`) | `trie_id_len()` = 33 | `2 + 10 + 33 + 2` = 47 | — (correct) |
| Host function (`promise_batch_action_add_gas_key_with_full_access`) | wire = 1953 | `2 + 10 + 1953 + 2` = 1967 | **+1920 bytes** |

Total exec-fee overcharge for 4 nonces: `4 × 1920 × gas_key_byte_exec_fee`.

The fee-parity test at `test-loop-tests/src/tests/gas_keys.rs` asserts `add_a_outcome.gas_burnt == add_b_outcome.gas_burnt` but only for `KeyType::ED25519`, where `trie_id_len()` = `len()` = 33 and the bug is invisible. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3155-3160)
```rust
        let exec_fee = gas_key_add_key_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
            num_nonces,
        );
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

**File:** core/parameters/src/cost.rs (L833-847)
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
}
```

**File:** core/parameters/src/cost.rs (L879-897)
```rust
pub fn gas_key_add_key_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,
    num_nonces: NonceIndex,
) -> GasKeyAddFee {
    let num_nonces = num_nonces as u64;
    let base =
        cfg.fee(ActionCosts::gas_key_nonce_write_base).exec_fee().checked_mul(num_nonces).unwrap();
    let nonce_key_len =
        access_key_key_len(account_id_len, public_key_len) + std::mem::size_of::<NonceIndex>();
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .exec_fee()
        .checked_mul((nonce_key_len + AccessKey::NONCE_VALUE_LEN) as u64)
        .unwrap()
        .checked_mul(num_nonces)
        .unwrap();
    GasKeyAddFee { base, per_byte }
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

**File:** runtime/runtime/src/access_keys.rs (L17-29)
```rust
fn access_key_storage_usage(
    fee_config: &RuntimeFeesConfig,
    public_key: &PublicKey,
    access_key: &AccessKey,
) -> StorageUsage {
    let storage_usage_config = &fee_config.storage_usage_config;
    // Use the on-trie identifier length, not the borsh-serialized pubkey
    // length: ML-DSA-65 access keys live in the trie as a SHA3-256 hash
    // (33 bytes incl. type tag), not as a 1953-byte full pubkey.
    public_key.trie_id_len() as u64
        + borsh::object_length(access_key).unwrap() as u64
        + storage_usage_config.num_extra_bytes_record
}
```

**File:** test-loop-tests/src/tests/gas_keys.rs (L1042-1045)
```rust
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();
```

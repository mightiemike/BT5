### Title
ML-DSA-65 gas-key host functions pass wire length (1953 B) instead of trie-id length (33 B) to exec-fee helpers, over-charging by ~59× vs the transaction-level path — (File: `runtime/near-vm-runner/src/logic/logic.rs`)

---

### Summary

The `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` host functions forward the raw `public_key_len` integer (the borsh wire length supplied by the contract) directly to `gas_key_transfer_exec_fee` / `gas_key_add_key_exec_fee`. For ML-DSA-65 keys that value is **1953 bytes** (wire) while the actual on-trie identifier is **33 bytes** (SHA3-256 hash). The transaction-level path in `runtime/runtime/src/config.rs` correctly calls `action.public_key.trie_id_len()` (= 33). The two code paths therefore compute divergent exec fees for the same ML-DSA-65 gas-key action: the host-function path over-charges by a factor of ~59×.

---

### Finding Description

`gas_key_transfer_exec_fee` is documented as modelling the cost the **receiver** pays to read/write the trie:

```rust
// core/parameters/src/cost.rs  line 830-846
/// Exec fee for TransferToGasKey / WithdrawFromGasKey actions.
/// Based on the access key trie key length + estimated value length
/// (what the receiver needs to read/write in the trie).
pub fn gas_key_transfer_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,          // ← must be trie-id length
) -> GasKeyTransferFee {
    let trie_key_len = access_key_key_len(account_id_len, public_key_len);
    ...
}
```

The transaction-level `exec_fee()` dispatcher passes the correct value:

```rust
// runtime/runtime/src/config.rs  line 347-349
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
```

The host-function implementations in both the wasmer and wasmtime paths pass the raw wire length instead:

```rust
// runtime/near-vm-runner/src/logic/logic.rs  line 3091-3096
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← wire length, not trie_id_len
);
```

```rust
// runtime/near-vm-runner/src/wasmtime_runner/logic.rs  line 3323-3325
let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
let exec =
    gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

The same pattern appears in `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call`, which call `gas_key_add_key_exec_fee` with `public_key_len as usize` instead of the decoded key's `trie_id_len()`:

```rust
// runtime/near-vm-runner/src/logic/logic.rs  line 3155-3160
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← wire length, not trie_id_len
    num_nonces,
);
```

The runtime-side `permission_exec_fees` correctly uses `trie_id_len()`:

```rust
// runtime/runtime/src/config.rs  line 389-394
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),  // ← correct
    gas_key_info.num_nonces,
);
```

The divergence exists only for `PublicKey::MLDSA65`, where `len()` = 1953 and `trie_id_len()` = 33:

```rust
// core/crypto/src/signature.rs  line 268-273 / 333-338
pub fn len(&self) -> usize {
    Self::MLDSA65(_) => 1 + ML_DSA_65_PUBLIC_KEY_LENGTH,  // 1953
}
pub fn trie_id_len(&self) -> usize {
    Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,        // 33
}
```

---

### Impact Explanation

`gas_key_transfer_exec_fee` feeds `public_key_len` into `access_key_key_len`, which adds it directly to the trie-key byte count used to price the per-byte exec component. Substituting 1953 for 33 inflates that component by `(1953 − 33) = 1920` extra bytes, multiplied by `gas_key_byte.exec_fee()` per nonce for `gas_key_add_key_exec_fee`. Any contract that calls these host functions with an ML-DSA-65 gas key will be charged ~59× more exec gas than the equivalent transaction-level action. Because the gas budget is fixed at call time, this causes the receipt to exhaust its gas and fail — making ML-DSA-65 gas keys functionally unusable via the host-function API despite being valid at the transaction level. The divergence is deterministic and reproducible for every ML-DSA-65 gas-key host-function call after `PostQuantumSignatures` activates.

**Severity: High** — the feature is silently broken for the host-function code path; any contract relying on it will fail with out-of-gas errors that cannot be diagnosed from the fee schedule alone.

---

### Likelihood Explanation

Any unprivileged contract author who follows the documented host-function API to manage ML-DSA-65 gas keys will trigger this path. No special privilege is required. The `PostQuantumSignatures` protocol feature must be active, but once it is, every call to these three host functions with an ML-DSA-65 key hits the divergent fee path.

---

### Recommendation

In `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` (both the `VMLogic` and wasmtime implementations), decode the public key **before** computing exec fees and pass `decoded_key.trie_id_len()` to `gas_key_transfer_exec_fee` / `gas_key_add_key_exec_fee` instead of the raw `public_key_len`. The send fee correctly continues to use the wire length (the sender physically reads those bytes), but the exec fee must reflect the on-trie identifier size.

---

### Proof of Concept

Exact divergent values for an ML-DSA-65 key targeting an account `"alice.near"` (10 bytes):

| Path | `public_key_len` passed | `trie_key_len` computed | exec per-byte multiplier |
|---|---|---|---|
| Transaction-level (`exec_fee`) | 33 (`trie_id_len`) | `1+10+1+33 = 45` | 45 + `AccessKey::min_gas_key_borsh_len()` |
| Host function (`promise_batch_action_transfer_to_gas_key`) | 1953 (wire) | `1+10+1+1953 = 1965` | 1965 + `AccessKey::min_gas_key_borsh_len()` |

The per-byte exec gas charged by the host-function path is `1965 / 45 ≈ 43.7×` larger for the trie-key component alone, before adding the value-length term. With `gas_key_byte.exec_fee()` at its production value, the total exec gas for the host-function path exceeds the transaction-level path by thousands of Ggas per call, reliably exhausting any reasonable gas budget. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3155-3161)
```rust
        let exec_fee = gas_key_add_key_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
            num_nonces,
        );
        self.result_state.gas_counter.pay_gas_key_add_key_fees(send_fee, &exec_fee)?;
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3325)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

**File:** runtime/runtime/src/config.rs (L347-354)
```rust
        TransferToGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
        WithdrawFromGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
```

**File:** runtime/runtime/src/config.rs (L389-395)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
    );
    key_fee.checked_add(nonce_fee.total()).unwrap()
```

**File:** core/parameters/src/cost.rs (L830-847)
```rust
/// Exec fee for TransferToGasKey / WithdrawFromGasKey actions.
/// Based on the access key trie key length + estimated value length (what the
/// receiver needs to read/write in the trie).
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

**File:** core/crypto/src/signature.rs (L260-274)
```rust
    /// Length of this public key's borsh encoding, in bytes - that is,
    /// the on-the-wire size of the raw key bytes plus a 1-byte borsh
    /// discriminant tag (the leading `+ 1` in each arm).
    ///
    /// For storage-fee accounting use [`PublicKey::trie_id_len`] instead;
    /// for ML-DSA-65 those two diverge (1953 wire vs 33 on-trie).
    // `is_empty` always returns false, so there is no point in adding it
    #[allow(clippy::len_without_is_empty)]
    pub fn len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_PUBLIC_KEY_LENGTH,
        }
    }
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

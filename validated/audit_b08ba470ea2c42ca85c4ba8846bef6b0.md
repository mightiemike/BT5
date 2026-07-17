### Title
Host-function gas-key fee path uses wire-format key length instead of trie-id length for ML-DSA-65 keys, causing ~59× fee overcharge vs transaction path — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

When a contract calls `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_withdraw_from_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, or `promise_batch_action_add_gas_key_with_function_call` with an ML-DSA-65 public key, the host-function path passes the raw borsh wire length of the key (1953 bytes) to `gas_key_transfer_send_fee` / `gas_key_transfer_exec_fee` / `gas_key_add_key_exec_fee`. The transaction path for the same actions correctly passes `action.public_key.trie_id_len()` (33 bytes for ML-DSA-65). For ED25519 and secp256k1 keys `len() == trie_id_len()`, so the divergence is invisible and the existing fee-parity test passes. For ML-DSA-65 keys the host-function path overcharges by a factor of ≈59 (1953 ÷ 33).

---

### Finding Description

**Two representations of the same key length exist in the codebase:**

- `PublicKey::len()` — borsh wire length: 1 + 1952 = **1953 bytes** for ML-DSA-65.
- `PublicKey::trie_id_len()` — on-trie identifier length: 1 + 32 = **33 bytes** for ML-DSA-65 (SHA3-256 hash form).

The documentation explicitly states that all storage-cost and fee code must use `trie_id_len()`:

> `PublicKey::trie_id_len()` is a new contract that all storage-cost code must respect. Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes. [1](#0-0) 

**Transaction path (correct):** `total_send_fees` and `exec_fee` in `runtime/runtime/src/config.rs` call the fee helpers with `action.public_key.trie_id_len()`:

```rust
TransferToGasKey(action) => {
    gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
        .total()
}
``` [2](#0-1) 

```rust
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
``` [3](#0-2) 

For `AddKey` with gas-key permission, `permission_exec_fees` also uses `trie_id_len()`:

```rust
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // ← correct
    gas_key_info.num_nonces,
);
``` [4](#0-3) 

**Host-function path (wrong):** `promise_batch_action_transfer_to_gas_key` in `logic.rs` passes the raw `public_key_len` parameter (the borsh wire length supplied by the WASM guest) to both fee helpers:

```rust
let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
// ...
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← wire length, not trie_id_len
);
``` [5](#0-4) 

The same pattern appears in the wasmtime runner:

```rust
let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
let exec =
    gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
``` [6](#0-5) 

The same mismatch exists in `promise_batch_action_add_gas_key_with_full_access`:

```rust
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← wire length, not trie_id_len
    num_nonces,
);
``` [7](#0-6) 

And in `promise_batch_action_add_gas_key_with_function_call`: [8](#0-7) 

**The fee helpers themselves are correct** — they accept a `public_key_len: usize` parameter and use it as the key-byte multiplier. The bug is entirely in what value is passed: [9](#0-8) [10](#0-9) 

**The existing fee-parity test does not cover ML-DSA-65 keys.** `test_gas_key_fee_parity` only uses `KeyType::ED25519`, for which `len() == trie_id_len() == 33`, so the divergence is zero and the test passes: [11](#0-10) 

**Exact divergent values for ML-DSA-65:**

| Path | Value passed as `public_key_len` | Source |
|---|---|---|
| Transaction (`config.rs`) | `trie_id_len()` = **33** | `PublicKey::trie_id_len()` |
| Host function (`logic.rs`) | `public_key_len` = **1953** | raw WASM guest parameter |

The per-byte fee multiplier is `gas_key_byte` (a configurable `ParameterCost`). For every `TransferToGasKey` / `WithdrawFromGasKey` / `AddKey(gas_key)` action emitted via host function with an ML-DSA-65 key, the charged gas is `(1953 - 33) × gas_key_byte` = **1920 × gas_key_byte** more than the equivalent transaction action.

---

### Impact Explanation

**High.** Any contract that manages ML-DSA-65 gas keys via host functions (`promise_batch_action_transfer_to_gas_key`, `promise_batch_action_withdraw_from_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`) will be charged approximately 59× more gas than the equivalent transaction-level action. This breaks the fee-parity invariant that the protocol guarantees between the two code paths, can cause gas exhaustion for contracts that budget based on the transaction-path cost, and creates a deterministic consensus-level divergence: the gas charged on-chain for a host-function call with an ML-DSA-65 key is wrong relative to the documented fee model.

---

### Likelihood Explanation

**Medium.** The `PostQuantumSignatures` feature is stabilized at protocol version 85 (current stable is 86), so ML-DSA-65 keys are live on mainnet. Any contract that programmatically manages gas keys and accepts ML-DSA-65 public keys as inputs will trigger the overcharge. The bug is silent — the action succeeds, but the gas charged is wrong — so it may go unnoticed until a contract runs out of gas or a developer compares host-function and transaction costs.

---

### Recommendation

In `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` (in both `runtime/near-vm-runner/src/logic/logic.rs` and `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`), decode the public key before computing fees and use `decoded_key.trie_id_len()` as the length argument:

```rust
let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
let decoded_key = public_key.decode()?;
let key_trie_len = decoded_key.trie_id_len();

let send = gas_key_transfer_send_fee(&self.fees_config, sir, key_trie_len);
let exec = gas_key_transfer_exec_fee(&self.fees_config, receiver_id.len(), key_trie_len);
// ...
self.ext.append_action_transfer_to_gas_key(receipt_idx, decoded_key, amount);
```

Extend `test_gas_key_fee_parity` to also cover `KeyType::MLDSA65` to prevent regression.

---

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with a borsh-encoded ML-DSA-65 public key (1953 bytes on the wire).
2. Also submit a `TransferToGasKey` transaction action for the same ML-DSA-65 key.
3. Compare `gas_burnt` on the action receipt for both paths.
4. The host-function receipt will show `gas_burnt` approximately 59× higher in the `gas_key_byte` cost component than the transaction receipt, because the fee helper receives `1953` instead of `33` as the key-length multiplier.

The existing `test_gas_key_fee_parity` test structure already provides the scaffolding; adding `KeyType::MLDSA65` as a second test case will reproduce the divergence. [12](#0-11) [13](#0-12)

### Citations

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```

**File:** runtime/runtime/src/config.rs (L114-117)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
            }
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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3085-3096)
```rust
        let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
        let amount = Balance::from_yoctonear(
            self.memory.get_u128(&mut self.result_state.gas_counter, amount_ptr)?,
        );
        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;
        let receiver_id = self.ext.get_receipt_receiver(receipt_idx);
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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3226-3231)
```rust
        let exec_fee = gas_key_add_key_exec_fee(
            &self.fees_config,
            receipt_receiver_id.len(),
            public_key_len as usize,
            num_nonces,
        );
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3325)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

**File:** core/parameters/src/cost.rs (L816-828)
```rust
pub fn gas_key_transfer_send_fee(
    cfg: &RuntimeFeesConfig,
    sender_is_receiver: bool,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).send_fee(sender_is_receiver);
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .send_fee(sender_is_receiver)
        .checked_mul(public_key_len as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
}
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

**File:** test-loop-tests/src/tests/gas_keys.rs (L1037-1061)
```rust
fn test_gas_key_fee_parity(mode: GasKeyKind) {
    let mut setup = setup_host_function_test();
    let account = setup.account.clone();

    let num_nonces: NonceIndex = 4;
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();

    let public_key_b_base64 = near_primitives_core::serialize::to_base64(
        &borsh::to_vec(&gas_key_b_signer.public_key()).unwrap(),
    );

    // Add gas key A via transaction, B via host function
    let add_a_outcome = setup.run_actions(vec![Action::AddKey(Box::new(AddKeyAction {
        public_key: gas_key_a_signer.public_key(),
        access_key: mode.access_key(num_nonces, &account),
    }))]);
    let add_b_outcome = setup.run_call_promise(serde_json::json!([
        {"batch_create": {"account_id": account.as_str()}, "id": 0},
        mode.add_action_json(num_nonces, &account, &public_key_b_base64),
    ]));
    assert_eq!(add_a_outcome.gas_burnt, add_b_outcome.gas_burnt);
    assert_eq!(add_a_outcome.tokens_burnt, add_b_outcome.tokens_burnt);
```

**File:** core/crypto/src/signature.rs (L259-339)
```rust
impl PublicKey {
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

    pub fn empty(key_type: KeyType) -> Self {
        match key_type {
            KeyType::ED25519 => {
                PublicKey::ED25519(ED25519PublicKey([0u8; ed25519_dalek::PUBLIC_KEY_LENGTH]))
            }
            KeyType::SECP256K1 => PublicKey::SECP256K1(Secp256K1PublicKey([0u8; 64])),
            KeyType::MLDSA65 => {
                PublicKey::MLDSA65(MlDsa65PublicKey(Box::new([0u8; ML_DSA_65_PUBLIC_KEY_LENGTH])))
            }
        }
    }

    pub fn key_type(&self) -> KeyType {
        match self {
            Self::ED25519(_) => KeyType::ED25519,
            Self::SECP256K1(_) => KeyType::SECP256K1,
            Self::MLDSA65(_) => KeyType::MLDSA65,
        }
    }

    fn key_tag(&self) -> KeyTag {
        match self {
            PublicKey::ED25519(_) => KeyTag::Ed25519,
            PublicKey::SECP256K1(_) => KeyTag::Secp256k1,
            PublicKey::MLDSA65(_) => KeyTag::MlDsa65Full,
        }
    }

    pub fn key_data(&self) -> &[u8] {
        match self {
            Self::ED25519(key) => key.as_ref(),
            Self::SECP256K1(key) => key.as_ref(),
            Self::MLDSA65(key) => key.as_ref(),
        }
    }

    pub fn unwrap_as_ed25519(&self) -> &ED25519PublicKey {
        match self {
            Self::ED25519(key) => key,
            Self::SECP256K1(_) | Self::MLDSA65(_) => panic!(),
        }
    }

    pub fn unwrap_as_secp256k1(&self) -> &Secp256K1PublicKey {
        match self {
            Self::SECP256K1(key) => key,
            Self::ED25519(_) | Self::MLDSA65(_) => panic!(),
        }
    }

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

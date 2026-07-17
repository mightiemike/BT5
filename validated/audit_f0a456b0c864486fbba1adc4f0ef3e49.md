### Title
ML-DSA-65 Gas Key Host Functions Use Wire Length Instead of Trie Length for Exec Fee Computation — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

### Summary

The host function implementations for gas key operations pass the raw wire-format `public_key_len` (1953 bytes for ML-DSA-65) to `gas_key_add_key_exec_fee` and `gas_key_transfer_exec_fee`, which are supposed to receive the **on-trie identifier length** (33 bytes for ML-DSA-65). The transaction path correctly calls `public_key.trie_id_len()` (33 bytes). The host function path never decodes the key before computing the exec fee, so it passes the borsh wire length instead. For ed25519/secp256k1 the two values are identical; for ML-DSA-65 they diverge by 1920 bytes, producing a massive exec-fee overcharge per nonce.

### Finding Description

The protocol design for ML-DSA-65 access keys stores a 33-byte SHA3-256 hash in the trie (tag `3` + 32-byte digest), not the 1953-byte borsh-encoded full pubkey. All trie-byte-priced fee paths are required to use `PublicKey::trie_id_len()` (33 for ML-DSA-65) rather than `PublicKey::len()` (1953 for ML-DSA-65). The documentation explicitly states: "Every storage-stake and trie-byte-priced fee path was updated to call `trie_id_len()`."

The **transaction path** in `runtime/runtime/src/config.rs` correctly honours this invariant:

```rust
// permission_exec_fees — transaction path
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // ← 33 bytes for ML-DSA-65
    gas_key_info.num_nonces,
);
```

```rust
// exec_fee — transaction path
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
```

The **host function path** in both `runtime/near-vm-runner/src/logic/logic.rs` and `runtime/near-vm-runner/src/wasmtime_runner/logic.rs` passes the raw `public_key_len` parameter instead:

```rust
// promise_batch_action_add_gas_key_with_full_access — host function path
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← 1953 bytes for ML-DSA-65 (wire length)
    num_nonces,
);
```

```rust
// promise_batch_action_transfer_to_gas_key — host function path
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← 1953 bytes for ML-DSA-65 (wire length)
);
```

The same pattern appears in `promise_batch_action_add_gas_key_with_function_call` in both runner files.

`gas_key_add_key_exec_fee` computes `access_key_key_len(account_id_len, public_key_len)` and multiplies by `gas_key_byte` exec fee per nonce. With `public_key_len = 1953` instead of `33`, the per-nonce overcharge is `(1953 − 33) × gas_key_byte_exec_fee = 1920 × gas_key_byte_exec_fee`. Multiplied by `num_nonces` (up to 65535), the total overcharge can be orders of magnitude larger than the 300 Tgas function-call gas limit, making ML-DSA-65 gas key registration via host functions effectively impossible.

The existing fee-parity test `test_gas_key_fee_parity` in `test-loop-tests/src/tests/gas_keys.rs` only exercises `KeyType::ED25519`, where `len() == trie_id_len()`, so the discrepancy is invisible in the current test suite.

### Impact Explanation

Any on-chain contract that calls `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, or `promise_batch_action_transfer_to_gas_key` with an ML-DSA-65 public key will be charged an exec fee computed against 1953 bytes instead of 33 bytes. The overcharge per nonce is 1920 × `gas_key_byte` exec fee. For any non-trivial `num_nonces`, this exceeds the per-receipt gas limit, causing the receipt to fail with out-of-gas. ML-DSA-65 gas keys are therefore unreachable via the host function path despite being valid at the protocol level. This breaks the fee-domain invariant ("trie-byte-priced fees use `trie_id_len()`") and makes a protocol feature (ML-DSA-65 gas keys) inaccessible to contracts.

### Likelihood Explanation

The `PostQuantumSignatures` feature is stabilised at protocol version 85. Any contract deployed after that version that attempts to register or fund an ML-DSA-65 gas key via host functions will trigger the overcharge. The call path is unprivileged: any user-deployed contract can invoke these host functions.

### Recommendation

After decoding the public key, use `decoded_key.trie_id_len()` for the exec fee argument in all three host function implementations. The send fee correctly uses the wire length (the sender physically transmits the full pubkey); only the exec fee must use the trie length.

```rust
// Decode first, then use trie_id_len for exec fee
let decoded_key = public_key.decode()?;
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    decoded_key.trie_id_len(),   // ← 33 for ML-DSA-65
    num_nonces,
);
// ...
self.ext.append_action_add_gas_key_with_full_access(receipt_idx, decoded_key, num_nonces);
```

Apply the same fix to `promise_batch_action_add_gas_key_with_function_call` and `promise_batch_action_transfer_to_gas_key` in both `logic/logic.rs` and `wasmtime_runner/logic.rs`. Add a variant of `test_gas_key_fee_parity` that uses `KeyType::MLDSA65` to guard the invariant.

### Proof of Concept

**Divergent values:**

| Path | `public_key_len` passed to exec-fee helper | Trie bytes actually written |
|---|---|---|
| Transaction (`permission_exec_fees`) | `public_key.trie_id_len()` = **33** | 33 |
| Host fn (`promise_batch_action_add_gas_key_with_full_access`) | `public_key_len as usize` = **1953** | 33 |

**Overcharge per nonce:** `(1953 − 33) × gas_key_byte_exec_fee = 1920 × gas_key_byte_exec_fee`

**Relevant code locations:**

Transaction path (correct): [1](#0-0) 

Host function path (wrong — `public_key_len as usize` instead of `trie_id_len()`): [2](#0-1) [3](#0-2) [4](#0-3) 

Wasmtime runner (same bug): [5](#0-4) [6](#0-5) [7](#0-6) 

Fee helper that requires trie length: [8](#0-7) 

`trie_id_len()` definition showing the 33 vs 1953 divergence: [9](#0-8) 

Fee-parity test that only covers ed25519 (gap): [10](#0-9)

### Citations

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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3092-3096)
```rust
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3326)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
    let burn_base = send.base;
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3499-3504)
```rust
    let exec_fee = gas_key_add_key_exec_fee(
        &ctx.fees_config,
        receipt_receiver_id.len(),
        public_key_len as usize,
        num_nonces,
    );
```

**File:** core/parameters/src/cost.rs (L879-898)
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
}
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

**File:** test-loop-tests/src/tests/gas_keys.rs (L1037-1046)
```rust
fn test_gas_key_fee_parity(mode: GasKeyKind) {
    let mut setup = setup_host_function_test();
    let account = setup.account.clone();

    let num_nonces: NonceIndex = 4;
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();

```

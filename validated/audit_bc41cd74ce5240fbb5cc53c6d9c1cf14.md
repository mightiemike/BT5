### Title
Gas Key Host Functions Use Raw Borsh `public_key_len` Instead of On-Trie `trie_id_len` for Fee Computation, Causing ~59× Overcharge for ML-DSA-65 Keys — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

Three gas-key host functions (`promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, `promise_batch_action_transfer_to_gas_key`) pass the raw caller-supplied `public_key_len` (the borsh-encoded wire length) directly into the per-byte fee helpers `gas_key_add_key_exec_fee` and `gas_key_transfer_{send,exec}_fee`. The transaction-path equivalents in `runtime/runtime/src/config.rs` correctly pass `public_key.trie_id_len()` instead. For ML-DSA-65 keys the two values diverge by a factor of ~59 (1953 borsh bytes vs. 33 on-trie bytes), producing a deterministic ~59× overcharge on the per-byte gas component whenever a contract adds or funds an ML-DSA-65 gas key via a host function.

---

### Finding Description

`PublicKey::trie_id_len()` was introduced precisely because ML-DSA-65 access keys are stored in the trie as a 33-byte SHA3-256 hash (`[tag=3] || hash`), not as the 1953-byte borsh-encoded full pubkey. Every storage-stake and trie-byte-priced fee path in the **transaction** route was updated to call `trie_id_len()`:

```rust
// runtime/runtime/src/config.rs – transaction path (correct)
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
// ...
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // ← 33 for ML-DSA-65
    gas_key_info.num_nonces,
);
``` [1](#0-0) [2](#0-1) 

The **host-function** path was not updated. All three gas-key host functions pass the raw `public_key_len` argument (the byte count of the borsh blob the contract wrote into wasm memory) directly to the same fee helpers:

```rust
// runtime/near-vm-runner/src/logic/logic.rs – host function path (wrong)
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← 1953 for ML-DSA-65, should be 33
    num_nonces,
);
``` [3](#0-2) [4](#0-3) [5](#0-4) 

The same pattern appears in the wasmtime runner: [6](#0-5) [7](#0-6) [8](#0-7) 

The fee helpers use `public_key_len` to compute the nonce trie-key length:

```rust
// core/parameters/src/cost.rs
let nonce_key_len =
    access_key_key_len(account_id_len, public_key_len) + std::mem::size_of::<NonceIndex>();
let per_byte = cfg.fee(ActionCosts::gas_key_byte).exec_fee()
    .checked_mul((nonce_key_len + AccessKey::NONCE_VALUE_LEN) as u64)
    .unwrap()
    .checked_mul(num_nonces)
    .unwrap();
``` [9](#0-8) 

For an ML-DSA-65 key the actual on-trie nonce key is 33 bytes wide, but the host-function path prices it as 1953 bytes — a ~59× overcharge on the per-byte component.

The `test_gas_key_fee_parity` integration test that verifies transaction-vs-host-function gas parity only exercises `KeyType::ED25519`, for which `len() == trie_id_len()`, so the divergence is invisible to the existing test suite: [10](#0-9) 

The design contract is documented explicitly:

> Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes. [11](#0-10) 

---

### Impact Explanation

Any contract that calls `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, or `promise_batch_action_transfer_to_gas_key` with an ML-DSA-65 public key is charged ~59× more gas on the per-byte component than the equivalent `AddKey`/`TransferToGasKey` transaction action. The exact divergent value is:

| Path | `public_key_len` used | per-byte multiplier |
|---|---|---|
| Transaction (`config.rs`) | `trie_id_len()` = **33** | correct |
| Host function (`logic.rs`) | `public_key_len` = **1953** | ~59× too high |

This breaks the protocol invariant that gas cost must be identical between the transaction and host-function paths for the same logical action. Contracts that budget gas for ML-DSA-65 gas-key operations based on the transaction-path cost will run out of gas when the same operation is performed via host function, causing silent action failures. The overcharge is deterministic and reproducible across all validators, so it does not cause consensus divergence — but it does make ML-DSA-65 gas keys effectively unusable from within contracts.

---

### Likelihood Explanation

Both `GasKeys` and `PostQuantumSignatures` are stabilized protocol features (the latter at protocol version 85). Any contract that attempts to manage ML-DSA-65 gas keys programmatically — a natural use case once PQ keys are live — will trigger the overcharge. The contract author controls the key type passed to the host function, so this is unprivileged-user-controlled input reaching the broken fee path.

---

### Recommendation

Replace `public_key_len as usize` with `public_key.decode()?.trie_id_len()` (or decode the key first and call `trie_id_len()` on the result) in all three gas-key host functions, in both `logic.rs` and `wasmtime_runner/logic.rs`. The decoded key is already available at the call site (it is passed to `append_action_*`), so no additional parsing is needed. Extend `test_gas_key_fee_parity` to cover `KeyType::MLDSA65` to prevent regression.

---

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_add_gas_key_with_full_access` with a borsh-encoded ML-DSA-65 public key (1953 bytes) and `num_nonces = 1`.
2. Observe the `gas_burnt` on the resulting action receipt.
3. Submit an equivalent `AddKey` transaction with the same ML-DSA-65 key and `GasKeyFullAccess { num_nonces: 1 }`.
4. Compare `gas_burnt` on the action receipt.

The host-function receipt will burn approximately `gas_key_byte.exec_fee() × (1953 − 33) × 1` more gas than the transaction receipt — a difference of ~1920 × `gas_key_byte.exec_fee()` per nonce. With `num_nonces = 4` (a typical value in the existing tests) the overcharge is ~7680 × `gas_key_byte.exec_fee()`.

### Citations

**File:** runtime/runtime/src/config.rs (L114-116)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
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

**File:** test-loop-tests/src/tests/gas_keys.rs (L1042-1045)
```rust
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();
```

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```

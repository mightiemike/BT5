### Title
Gas-key exec-fee computed from raw wire length instead of on-trie identifier length in host-function path, causing ~59× overcharge for ML-DSA-65 keys — (File: `runtime/near-vm-runner/src/logic/logic.rs`)

---

### Summary

The host-function implementations of `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` pass the raw caller-supplied `public_key_len` (the borsh wire length of the key) directly into `gas_key_add_key_exec_fee`. For ML-DSA-65 keys this is 1953 bytes, whereas the actual on-trie nonce-key uses the 33-byte SHA3-256 hash form. The transaction path correctly calls `public_key.trie_id_len()` (= 33). The divergence is ~1920 bytes per nonce, producing an exec-fee that is ~59× too high for any contract that adds an ML-DSA-65 gas key via the host function, breaking the fee-parity invariant that the codebase explicitly tests and documents.

---

### Finding Description

**Correct path — transaction-level fee computation (`runtime/runtime/src/config.rs`)**

```rust
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // ← 33 for ML-DSA-65
    gas_key_info.num_nonces,
);
``` [1](#0-0) 

**Broken path — host-function exec-fee computation (`runtime/near-vm-runner/src/logic/logic.rs`)**

```rust
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← 1953 for ML-DSA-65 (raw wire bytes)
    num_nonces,
);
``` [2](#0-1) 

The same mistake appears in the `GasKeyFunctionCall` variant and in the wasmtime runner: [3](#0-2) [4](#0-3) [5](#0-4) 

**Why the values diverge**

`gas_key_add_key_exec_fee` computes the per-nonce trie-key length as:

```
nonce_key_len = access_key_key_len(account_id_len, public_key_len) + size_of::<NonceIndex>()
``` [6](#0-5) 

For ML-DSA-65, the actual on-trie key uses the 33-byte hash form (`[tag=3] || sha3_256(domain || raw_pubkey)`), not the 1953-byte borsh-encoded full pubkey. `PublicKey::trie_id_len()` returns 33; `PublicKey::len()` (and the raw wire bytes the contract passes) returns 1953. [7](#0-6) 

The design document explicitly calls this out as a contract all storage-cost code must respect:

> `PublicKey::trie_id_len()` is a new contract that all storage-cost code must respect. Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes. [8](#0-7) 

**The fee-parity invariant is explicitly tested — but only for ED25519**

`test_gas_key_fee_parity` asserts that adding a gas key via transaction and via host function produces identical `gas_burnt` and `tokens_burnt`. It uses only `KeyType::ED25519`, for which `len() == trie_id_len() == 33`, so the bug is invisible to the test suite. [9](#0-8) 

---

### Impact Explanation

For an ML-DSA-65 gas key with `num_nonces = N` and a typical account-id length of `A`:

| Path | `public_key_len` used | `nonce_key_len` | exec-fee multiplier |
|---|---|---|---|
| Transaction | 33 (trie_id_len) | A + 37 | correct |
| Host function | 1953 (wire len) | A + 1957 | ~53× larger (for A=10) |

The exec fee is charged against `used_gas` on the receipt. A contract that attaches gas sized for the correct fee will exhaust gas and the action will fail. A contract that attaches enough gas to cover the inflated fee pays ~53–59× more than the equivalent transaction, making ML-DSA-65 gas keys via host functions economically unusable and breaking the documented fee-parity invariant between the two code paths.

---

### Likelihood Explanation

The `PostQuantumSignatures` feature is stabilized at protocol version 85. Any contract deployed after that version can call `promise_batch_action_add_gas_key_with_full_access` with an ML-DSA-65 key. No privileged role is required. The trigger is a normal contract call with a well-formed ML-DSA-65 public key, which is a valid, accepted input after the feature activates.

---

### Recommendation

Replace `public_key_len as usize` with the decoded key's `trie_id_len()` in both host-function implementations. The public key is already decoded before the fee call (via `public_key.decode()?`), so `trie_id_len()` is available:

```rust
// After: let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
// and:   let decoded_key = public_key.decode()?;
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    decoded_key.trie_id_len(),   // ← was: public_key_len as usize
    num_nonces,
);
```

Apply the same fix in `promise_batch_action_add_gas_key_with_function_call` in both `logic.rs` and `wasmtime_runner/logic.rs`. Extend `test_gas_key_fee_parity` to cover `KeyType::MLDSA65`.

---

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_add_gas_key_with_full_access` with a borsh-encoded ML-DSA-65 public key (1953 bytes).
2. Submit the same `AddKey` action as a direct transaction with the same ML-DSA-65 key.
3. Compare `gas_burnt` on the two execution outcomes.
4. The host-function receipt burns ~59× more gas than the transaction receipt for the same logical operation, violating the invariant asserted by `test_gas_key_fee_parity`.

The exact divergent Borsh-level value: `public_key_len = 1953` (ML-DSA-65 wire length) is used where `trie_id_len() = 33` (ML-DSA-65 hash-form length) is required, producing a nonce-key-length error of 1920 bytes per nonce multiplied by `gas_key_byte` exec rate multiplied by `num_nonces`.

### Citations

**File:** runtime/runtime/src/config.rs (L389-394)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
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

**File:** core/parameters/src/cost.rs (L888-889)
```rust
    let nonce_key_len =
        access_key_key_len(account_id_len, public_key_len) + std::mem::size_of::<NonceIndex>();
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

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
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

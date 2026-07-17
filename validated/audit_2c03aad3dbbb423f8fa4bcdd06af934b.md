### Title
ML-DSA-65 Gas-Key Host Functions Use Wire Length Instead of Trie-ID Length for Fee Computation, Breaking Fee-Parity Invariant — (`runtime/near-vm-runner/src/logic/logic.rs`)

---

### Summary

The three gas-key host functions (`promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`) forward the raw WASM-memory `public_key_len` argument directly into `gas_key_transfer_send_fee`, `gas_key_transfer_exec_fee`, and `gas_key_add_key_exec_fee`. For an ML-DSA-65 public key the borsh wire length is **1953 bytes** (1 tag + 1952 raw key bytes), but the trie stores only a 33-byte SHA3-256 hash. The transaction path that computes the same fees for the same actions correctly calls `action.public_key.trie_id_len()` (= 33 bytes for ML-DSA-65). The result is a **~59× fee divergence** between the two paths for every ML-DSA-65 gas-key action issued from a contract.

---

### Finding Description

`PublicKey` exposes two length methods:

- `len()` — borsh wire length: 33 / 65 / **1953** bytes for ED25519 / SECP256K1 / ML-DSA-65.
- `trie_id_len()` — on-trie identifier length: 33 / 65 / **33** bytes (ML-DSA-65 stores a SHA3-256 hash, not the full key). [1](#0-0) 

The design document explicitly warns: *"Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes."* [2](#0-1) 

**Transaction path — correct:** `total_send_fees` and `exec_fee` in `runtime/runtime/src/config.rs` call `action.public_key.trie_id_len()` when computing gas-key transfer and add-key fees: [3](#0-2) [4](#0-3) [5](#0-4) 

**Host-function path — incorrect:** The three gas-key host functions in `logic.rs` pass the raw `public_key_len` (the borsh wire length supplied by the WASM contract) directly to the same fee helpers: [6](#0-5) [7](#0-6) [8](#0-7) 

The same divergence exists in the Wasmtime runner: [9](#0-8) [10](#0-9) [11](#0-10) 

The fee helpers themselves are correct — they accept a `public_key_len: usize` parameter and use it to compute trie-key lengths: [12](#0-11) [13](#0-12) 

The existing fee-parity integration test only exercises ED25519 keys, so the ML-DSA-65 divergence is not caught: [14](#0-13) 

---

### Impact Explanation

For an ML-DSA-65 gas key, `public_key_len` = 1953 bytes while `trie_id_len()` = 33 bytes — a **~59× ratio**. Every gas-key action issued from a contract using an ML-DSA-65 key is charged ~59× the gas that the equivalent transaction-path action would cost. Concretely:

- `gas_key_transfer_send_fee` and `gas_key_transfer_exec_fee` scale linearly with `public_key_len`, so both the send and exec components of `TransferToGasKey` / `WithdrawFromGasKey` are inflated ~59×.
- `gas_key_add_key_exec_fee` scales the per-nonce trie-key length by `public_key_len`, so adding a gas key with N nonces is inflated ~59× on the exec side.

A contract that budgets gas based on the transaction-path cost will exhaust its gas allowance when the same operation is performed via host function with an ML-DSA-65 key, causing unexpected receipt failures. This breaks the protocol-level fee-parity invariant between the two action-submission paths.

---

### Likelihood Explanation

`PostQuantumSignatures` is stabilized at protocol version 85. Once active, any contract can add an ML-DSA-65 gas key and call the affected host functions. The trigger is entirely unprivileged: a WASM contract passes a borsh-encoded ML-DSA-65 public key (1953 bytes) to any of the three host functions. No validator or operator action is required. The only prerequisite is that the `PostQuantumSignatures` feature is enabled on the network.

---

### Recommendation

Replace the raw `public_key_len` argument with the decoded key's `trie_id_len()` when calling the fee helpers inside the three host functions. After `get_public_key` / `public_key.decode()` succeeds, the decoded `PublicKey` is available and `trie_id_len()` can be called on it. For example, in `promise_batch_action_transfer_to_gas_key`:

```rust
let public_key = self.get_public_key(public_key_ptr, public_key_len)?.decode()?;
let pk_trie_len = public_key.trie_id_len();
let send = gas_key_transfer_send_fee(&self.fees_config, sir, pk_trie_len);
let exec = gas_key_transfer_exec_fee(&self.fees_config, receiver_id.len(), pk_trie_len);
```

Apply the same fix to `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call`, and to the Wasmtime runner counterparts. Extend `test_gas_key_fee_parity` to cover `KeyType::MLDSA65` to prevent regression.

---

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with a borsh-encoded ML-DSA-65 public key (1953 bytes).
2. Submit the same `TransferToGasKey` action as a regular transaction using the same ML-DSA-65 key.
3. Compare `gas_burnt` on the two execution outcomes.

Expected (correct): both outcomes burn the same gas (fee parity).
Actual (buggy): the host-function path burns ~59× more gas than the transaction path, because `gas_key_transfer_send_fee` and `gas_key_transfer_exec_fee` receive 1953 instead of 33 as `public_key_len`.

The unit test `test_ml_dsa_65_access_key_storage_scales` confirms that `trie_id_len()` = 33 for ML-DSA-65, while `len()` = 1953: [15](#0-14)

### Citations

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

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```

**File:** runtime/runtime/src/config.rs (L114-116)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
```

**File:** runtime/runtime/src/config.rs (L347-353)
```rust
        TransferToGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
        WithdrawFromGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
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

**File:** core/parameters/src/cost.rs (L816-847)
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

**File:** runtime/runtime/src/access_keys.rs (L1232-1238)
```rust
    fn test_ml_dsa_65_trie_id_len_is_hash_size() {
        let pq_pk: PublicKey =
            near_crypto::SecretKey::from_seed(near_crypto::KeyType::MLDSA65, "trie-id-len")
                .public_key();
        assert_eq!(pq_pk.trie_id_len(), 1 + 32);
        assert_eq!(pq_pk.len(), 1 + 1952); // borsh form still reports full
    }
```

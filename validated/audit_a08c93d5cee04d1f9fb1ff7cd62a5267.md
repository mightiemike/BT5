### Title
Gas Key Exec-Fee Representation Mismatch: Borsh Length Used Instead of Trie-ID Length in Host-Function Path — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

### Summary

The `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` host functions pass the raw Borsh-wire `public_key_len` (as supplied by the WASM contract) directly into `gas_key_add_key_exec_fee()`. The exec-fee function interprets this value as the on-trie key length. For ML-DSA-65 keys the two representations diverge by a factor of ~59× (1953 bytes Borsh vs. 33 bytes trie-hash), causing a massive exec-fee overcharge and breaking the fee-parity invariant that the transaction path upholds by calling `public_key.trie_id_len()`.

### Finding Description

**Transaction path (correct)** — `runtime/runtime/src/config.rs`, `permission_exec_fees`:

```rust
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // ← 33 bytes for ML-DSA-65
    gas_key_info.num_nonces,
);
``` [1](#0-0) 

**Host-function path (wrong)** — `runtime/near-vm-runner/src/logic/logic.rs`, `promise_batch_action_add_gas_key_with_full_access`:

```rust
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← 1953 bytes for ML-DSA-65 (Borsh wire length)
    num_nonces,
);
``` [2](#0-1) 

The same pattern appears in the wasmtime runner: [3](#0-2) 

And in `promise_batch_action_add_gas_key_with_function_call`: [4](#0-3) 

`gas_key_add_key_exec_fee` uses `public_key_len` to compute the per-nonce trie key length:

```rust
let nonce_key_len =
    access_key_key_len(account_id_len, public_key_len) + std::mem::size_of::<NonceIndex>();
let per_byte = cfg.fee(ActionCosts::gas_key_byte).exec_fee()
    .checked_mul((nonce_key_len + AccessKey::NONCE_VALUE_LEN) as u64)
    .unwrap()
    .checked_mul(num_nonces)
    .unwrap();
``` [5](#0-4) 

This value is supposed to model the actual trie key length. For ML-DSA-65, the trie stores a SHA3-256 hash (33 bytes including the type tag), not the 1953-byte Borsh-encoded full key. The documentation explicitly states this contract:

> `PublicKey::trie_id_len()` is a new contract that all storage-cost code must respect. Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1920 bytes. [6](#0-5) 

The storage-usage accounting in `access_key_storage_usage` and `gas_key_storage_cost` correctly uses `trie_id_len()`: [7](#0-6) 

The host-function fee path does not.

### Impact Explanation

For an ML-DSA-65 gas key with `num_nonces = 16`:

- Correct exec fee multiplier: `33 + sizeof(NonceIndex)` = 35 bytes per nonce
- Actual exec fee multiplier used: `1953 + sizeof(NonceIndex)` = 1955 bytes per nonce
- Overcharge per nonce: **1920 bytes × `gas_key_byte` exec fee**
- Total overcharge: **1920 × 16 × `gas_key_byte_exec_fee`** gas units

At the shipped `gas_key_byte` exec fee this is a ~59× overcharge on the per-byte component of the exec fee. A contract that attaches 300 TGas (the protocol maximum) to a function call that creates an ML-DSA-65 gas key via the host function will exhaust its gas budget far earlier than the same operation submitted as a direct transaction, causing `GasExceeded` failures for otherwise valid contract logic.

The fee-parity invariant — verified by `test_gas_key_fee_parity` only for ED25519 keys — is broken for ML-DSA-65 keys: [8](#0-7) 

### Likelihood Explanation

Both `GasKeys` (`gas_key_host_fns`) and `PostQuantumSignatures` (`ml_dsa_65_verification_cost = 100000000000`) are enabled in the current shipped protocol versions (≥ 85). Any unprivileged contract developer can call `promise_batch_action_add_gas_key_with_full_access` with an ML-DSA-65 public key and trigger the overcharge. No privileged role is required.

### Recommendation

Replace `public_key_len as usize` with the decoded key's `trie_id_len()` in all three host-function call sites:

```rust
// After decoding the public key:
let public_key_decoded = public_key.decode()?;
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_decoded.trie_id_len(),  // ← use trie representation length
    num_nonces,
);
```

Apply the same fix to `promise_batch_action_add_gas_key_with_function_call` and the wasmtime runner equivalents. Extend `test_gas_key_fee_parity` to cover ML-DSA-65 keys.

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_add_gas_key_with_full_access` with a Borsh-encoded ML-DSA-65 public key (1953 bytes) and `num_nonces = 16`.
2. Submit the same `AddKey` action as a direct `SignedTransaction` with the same ML-DSA-65 key.
3. Observe that the host-function path charges `~59×` more exec gas for the `gas_key_byte` component than the transaction path, causing the contract call to fail with `GasExceeded` while the direct transaction succeeds.

The exact divergent value is `(1953 − 33) × num_nonces × gas_key_byte_exec_fee` = `1920 × 16 × gas_key_byte_exec_fee` gas units of overcharge, traceable to `public_key_len as usize` at: [9](#0-8) [10](#0-9)

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

**File:** core/parameters/src/cost.rs (L888-897)
```rust
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

**File:** docs/architecture/how/post_quantum_signatures.md (L189-192)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.

```

**File:** runtime/runtime/src/access_keys.rs (L17-44)
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

fn gas_key_storage_cost(
    fee_config: &RuntimeFeesConfig,
    public_key: &PublicKey,
    access_key: &AccessKey,
    num_nonces: NonceIndex,
) -> StorageUsage {
    let storage_config = &fee_config.storage_usage_config;
    let per_nonce_value_size = borsh::object_length(&(0 as Nonce)).unwrap() as u64;
    let per_nonce_key_size = public_key.trie_id_len() as u64 + size_of::<NonceIndex>() as u64;

    num_nonces as u64
        * (per_nonce_key_size + per_nonce_value_size + storage_config.num_extra_bytes_record)
        + access_key_storage_usage(fee_config, public_key, access_key)
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

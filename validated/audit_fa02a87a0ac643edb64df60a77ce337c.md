### Title
ML-DSA-65 Gas-Key Nonce Exec Fee Uses Borsh Wire Length Instead of Trie-ID Length in Host-Function Path — (`runtime/near-vm-runner/src/logic/logic.rs`)

---

### Summary

When a smart contract calls `promise_batch_action_add_gas_key_with_full_access` or `promise_batch_action_add_gas_key_with_function_call` with an ML-DSA-65 public key, the exec fee for writing gas-key nonce trie entries is computed using the borsh wire length of the key (1953 bytes) instead of the on-trie identifier length (33 bytes). The transaction path correctly calls `public_key.trie_id_len()`, but the host-function path passes the raw `public_key_len` parameter (the borsh-encoded byte count from contract memory) directly to `gas_key_add_key_exec_fee`. This produces a ~59× overcharge on the per-nonce exec fee for every ML-DSA-65 gas key added via a host function.

---

### Finding Description

`gas_key_add_key_exec_fee` computes the exec fee for writing `num_nonces` trie entries. Its `public_key_len` parameter is used to derive the actual on-trie key length:

```rust
// core/parameters/src/cost.rs:888-889
let nonce_key_len =
    access_key_key_len(account_id_len, public_key_len) + std::mem::size_of::<NonceIndex>();
```

The function's contract is that `public_key_len` must be the **on-trie identifier length** — i.e., `trie_id_len()` — because that is the number of bytes actually written to the trie per nonce entry.

The **transaction path** in `permission_exec_fees` correctly passes `trie_id_len()`:

```rust
// runtime/runtime/src/config.rs:389-394
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // ← 33 bytes for ML-DSA-65
    gas_key_info.num_nonces,
);
```

The **host-function path** in `promise_batch_action_add_gas_key_with_full_access` passes `public_key_len as usize` — the raw borsh wire length of the key as supplied by the calling contract:

```rust
// runtime/near-vm-runner/src/logic/logic.rs:3155-3160
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← 1953 bytes for ML-DSA-65 (borsh wire length)
    num_nonces,
);
```

The same substitution appears in the wasmtime shim:

```rust
// runtime/near-vm-runner/src/wasmtime_runner/logic.rs:3395-3400
let exec_fee = gas_key_add_key_exec_fee(
    &ctx.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← same wrong domain
    num_nonces,
);
```

For ML-DSA-65, `trie_id_len()` = 33 (tag + 32-byte SHA3-256 hash) while the borsh wire length = 1953 (tag + 1952-byte raw pubkey). The `access_key_key_len` helper adds the account-id prefix, so the divergence propagates directly into `nonce_key_len` and then into the per-byte exec fee multiplied by `num_nonces`.

The same wrong-domain substitution also appears in the `promise_batch_action_transfer_to_gas_key` host function, which passes `public_key_len as usize` to both `gas_key_transfer_send_fee` and `gas_key_transfer_exec_fee`:

```rust
// runtime/near-vm-runner/src/logic/logic.rs:3091-3096
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← wire length, not trie length
);
```

The existing fee-parity integration test (`test_gas_key_fee_parity`) only exercises ED25519 keys, for which `len()` and `trie_id_len()` are identical (both 33 bytes), so the divergence is invisible in the test suite.

---

### Impact Explanation

Every contract that adds or funds an ML-DSA-65 gas key via a host function is overcharged by a factor of approximately `1953 / 33 ≈ 59×` on the per-byte exec component of the nonce-write fee. With a default of, say, 4 nonces, the overcharge is `59 × 4 = 236×` the correct exec fee for that component. This breaks the protocol invariant that the same on-chain operation costs the same gas regardless of whether it is initiated by a transaction or a host function. It makes ML-DSA-65 gas keys economically unviable when created from contracts, and it silently diverges from the documented fee model.

---

### Likelihood Explanation

The `PostQuantumSignatures` feature is stabilized at protocol version 85. Once active, any deployed contract can call `promise_batch_action_add_gas_key_with_full_access` with an ML-DSA-65 key. No privileged role is required. The overcharge is deterministic and reproducible on every such call. The fee-parity test gap means no existing CI check catches the regression.

---

### Recommendation

Replace `public_key_len as usize` with `public_key.trie_id_len()` (after decoding the key) in every host-function call site that feeds into `gas_key_add_key_exec_fee`, `gas_key_transfer_exec_fee`, and `gas_key_transfer_send_fee`. The decoded `PublicKey` is already available at those call sites (via `public_key.decode()?`), so `trie_id_len()` can be called on it before the fee computation. This mirrors the correct pattern already used in the transaction path.

---

### Proof of Concept

**Transaction path (correct):** [1](#0-0) 

**Host-function path (wrong domain — wire length instead of trie length):** [2](#0-1) 

**Wasmtime shim (same wrong domain):** [3](#0-2) 

**`gas_key_add_key_exec_fee` uses `public_key_len` as the on-trie key length:** [4](#0-3) 

**`trie_id_len()` for ML-DSA-65 returns 33 (hash form), not 1953 (wire form):** [5](#0-4) 

**Fee-parity test only covers ED25519, missing the ML-DSA-65 divergence:** [6](#0-5)

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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3395-3400)
```rust
    let exec_fee = gas_key_add_key_exec_fee(
        &ctx.fees_config,
        receiver_id.len(),
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

**File:** core/crypto/src/signature.rs (L333-338)
```rust
    pub fn trie_id_len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,
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

### Title
Gas-key nonce-write exec fee uses wire-encoded key length instead of on-trie identifier length for ML-DSA-65 keys in host functions - (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

The host functions `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` pass the raw wire-encoded `public_key_len` (1953 bytes for ML-DSA-65) to `gas_key_add_key_exec_fee()`, which uses it to price the per-nonce trie-write cost. The correct value is the on-trie identifier length (`trie_id_len()` = 33 bytes for ML-DSA-65). The regular-transaction path (`permission_exec_fees`) correctly calls `public_key.trie_id_len()`. The two paths diverge by 1920 bytes per nonce for every ML-DSA-65 gas key added via a contract host function, causing a ~59× overcharge that can exhaust gas and block the operation entirely.

---

### Finding Description

`gas_key_add_key_exec_fee` is documented as pricing the cost of writing `num_nonces` trie entries, each with a key of `access_key_key_len(account_id_len, public_key_len) + sizeof(NonceIndex)` bytes. The nonce trie key uses the **on-trie identifier** of the public key — for ML-DSA-65 that is a 33-byte SHA3-256 hash (`[tag=3] || sha3_256(domain || raw_pubkey)`), not the 1953-byte borsh-encoded full pubkey.

**Correct path — regular transaction (`runtime/runtime/src/config.rs`):**

```rust
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // ← 33 for ML-DSA-65
    gas_key_info.num_nonces,
);
``` [1](#0-0) 

**Buggy path — host function (`runtime/near-vm-runner/src/logic/logic.rs`):**

```rust
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // ← 1953 for ML-DSA-65 (wire length)
    num_nonces,
);
``` [2](#0-1) 

The same wrong value is used in the Wasmtime backend: [3](#0-2) 

And in `promise_batch_action_add_gas_key_with_function_call`: [4](#0-3) 

The divergence exists only for `PublicKey::MLDSA65`, where `len()` = 1953 and `trie_id_len()` = 33: [5](#0-4) 

The protocol documentation explicitly states that every storage-stake and trie-byte-priced fee path must use `trie_id_len()`: [6](#0-5) 

The `gas_key_add_key_exec_fee` function computes:

```
nonce_key_len = access_key_key_len(account_id_len, public_key_len) + sizeof(NonceIndex)
per_byte = gas_key_byte_exec * (nonce_key_len + NONCE_VALUE_LEN) * num_nonces
``` [7](#0-6) 

With `public_key_len = 1953` instead of `33`, `nonce_key_len` is inflated by 1920 bytes per nonce. With `num_nonces` up to 1024 (`MAX_NONCES_FOR_GAS_KEY`), the total overcharge is up to `1920 × 1024 × gas_key_byte_exec_fee` gas units. [8](#0-7) 

---

### Impact Explanation

Any contract that calls `promise_batch_action_add_gas_key_with_full_access` or `promise_batch_action_add_gas_key_with_function_call` with an ML-DSA-65 public key after `PostQuantumSignatures` activates will be charged ~59× the correct exec fee for the nonce-write component. With `num_nonces = 1024`, the overcharge is ~1,966,080 × `gas_key_byte_exec_fee` gas units. This will cause the host function call to fail with an out-of-gas error even when the contract has sufficient gas for the correctly-priced operation, permanently blocking ML-DSA-65 gas key creation via contract host functions. The same AddKey action submitted as a direct transaction succeeds at the correct (lower) cost, creating a broken protocol invariant: identical state transitions cost different amounts depending on submission path.

---

### Likelihood Explanation

The bug is triggered by any contract that calls either gas-key host function with an ML-DSA-65 public key. This is a straightforward, documented use case once `PostQuantumSignatures` is active. No privileged role is required — any unprivileged account can deploy a contract and invoke the host function. The divergence is deterministic and reproducible on every invocation.

---

### Recommendation

Replace `public_key_len as usize` with `public_key.decode()?.trie_id_len()` in both host function implementations, mirroring the correct call in `permission_exec_fees`:

```rust
// After: let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
let decoded_key = public_key.decode()?;
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    decoded_key.trie_id_len(),   // on-trie identifier length, not wire length
    num_nonces,
);
// Pass decoded_key to append_action instead of calling public_key.decode()? again
```

Apply the same fix to both `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` in both `runtime/near-vm-runner/src/logic/logic.rs` and `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`.

---

### Proof of Concept

The fee divergence is exact and computable from the existing constants:

- `PublicKey::MLDSA65.len()` = 1 + 1952 = **1953** (wire, used by host fn)
- `PublicKey::MLDSA65.trie_id_len()` = 1 + 32 = **33** (on-trie, used by tx path) [9](#0-8) 

For `num_nonces = 10`, `account_id_len = 10` (e.g. `"alice.near"`):

- Correct `nonce_key_len` = `access_key_key_len(10, 33) + 2` = `(1 + 10 + 1 + 33) + 2` = **47 bytes**
- Buggy `nonce_key_len` = `access_key_key_len(10, 1953) + 2` = `(1 + 10 + 1 + 1953) + 2` = **1967 bytes**

The host function charges for 1967-byte nonce keys; the actual trie writes use 47-byte keys. The overcharge ratio is ~41.8× for this example, rising to ~59× for a 1-character account ID. A contract deploying an ML-DSA-65 gas key with `num_nonces = 1024` via host function will exhaust gas at a budget that would comfortably cover the same action submitted as a direct transaction.

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

**File:** core/primitives-core/src/account.rs (L589-589)
```rust
    pub const MAX_NONCES_FOR_GAS_KEY: NonceIndex = 1024;
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

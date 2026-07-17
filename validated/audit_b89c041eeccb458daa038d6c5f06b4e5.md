### Title
ML-DSA-65 Gas-Key Exec-Fee Uses Wire-Format Key Length Instead of Trie-ID Length in Host-Function Path — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

When a contract adds an ML-DSA-65 gas key via the host functions `promise_batch_action_add_gas_key_with_full_access` or `promise_batch_action_add_gas_key_with_function_call`, the exec-fee helper `gas_key_add_key_exec_fee` is called with the raw wire-format key length (`public_key_len as usize` = 1953 bytes for ML-DSA-65) instead of the on-trie identifier length (`trie_id_len()` = 33 bytes). The transaction path (`permission_exec_fees`) correctly uses `trie_id_len()`. The two paths diverge by 1920 bytes for every ML-DSA-65 gas key, producing a ~59× exec-fee overcharge via the host-function path and breaking the fee-domain invariant that the same on-chain operation costs the same regardless of how it is invoked.

---

### Finding Description

`gas_key_add_key_exec_fee` (`core/parameters/src/cost.rs:879`) computes the per-nonce trie-write cost as:

```
nonce_key_len = access_key_key_len(account_id_len, public_key_len) + sizeof(NonceIndex)
```

The `public_key_len` argument is supposed to be the **on-trie identifier length** because the function models the bytes the receiver must write into the trie. For ML-DSA-65, the trie stores a 33-byte SHA3-256 hash (`[tag=3] || sha3_256(domain || raw_pubkey)`), not the 1953-byte borsh-encoded full pubkey.

**Transaction path** (`runtime/runtime/src/config.rs:389–394`) — correct:

```rust
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // 33 bytes for ML-DSA-65
    gas_key_info.num_nonces,
);
```

**Host-function path** (`runtime/near-vm-runner/src/logic/logic.rs:3155–3160` and `3226–3231`) — wrong:

```rust
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // 1953 bytes for ML-DSA-65 (wire format)
    num_nonces,
);
```

The same wrong pattern appears in the wasmtime runner at `runtime/near-vm-runner/src/wasmtime_runner/logic.rs:3395–3400` and `3499–3504`.

`public_key_len` in the host-function signature is the borsh-encoded length of the key as the contract wrote it into guest memory — 1953 bytes for ML-DSA-65 (`1 tag + 1952 raw bytes`). `trie_id_len()` for ML-DSA-65 returns 33 (`1 tag + 32-byte SHA3-256 hash`). The divergence is 1920 bytes per nonce per key.

---

### Impact Explanation

For each nonce slot, `nonce_key_len` is inflated by 1920 bytes. With `N` nonces, the per-byte exec fee is multiplied by `(access_key_key_len(account_id_len, 1953) + 2) / (access_key_key_len(account_id_len, 33) + 2)` — roughly 59× for a typical account-id length. The overcharge is proportional to `num_nonces`.

Concrete effect:
- A contract adding an ML-DSA-65 gas key with 4 nonces via host function is charged ~59× more exec gas than the same `AddKey` action submitted as a direct transaction.
- The fee invariant documented in `docs/architecture/how/post_quantum_signatures.md` §5 ("Every storage-stake and trie-byte-priced fee path was updated to call `trie_id_len()`") is violated for the host-function path.
- The existing parity test (`test_gas_key_fee_parity` in `test-loop-tests/src/tests/gas_keys.rs:1037`) only exercises ED25519 keys, so it does not catch this divergence.
- Any contract that creates ML-DSA-65 gas keys via host functions will either fail with insufficient gas or burn far more gas than expected, making the feature unusable via the contract API.

---

### Likelihood Explanation

The `PostQuantumSignatures` feature is stabilized at protocol version 85. Once active, any unprivileged user can deploy a contract that calls `promise_batch_action_add_gas_key_with_full_access` with an ML-DSA-65 public key. The overcharge is deterministic and reproducible on every such call. No special privilege is required beyond the ability to deploy and call a contract.

---

### Recommendation

In both `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` (in both `logic.rs` and `wasmtime_runner/logic.rs`), replace `public_key_len as usize` with `public_key.decode()?.trie_id_len()` (or decode the key first and call `trie_id_len()` on the decoded `PublicKey`) when computing the exec fee:

```rust
let decoded_pk = public_key.decode()?;
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    decoded_pk.trie_id_len(),   // trie-id length, not wire length
    num_nonces,
);
// then use decoded_pk below
self.ext.append_action_add_gas_key_with_full_access(receipt_idx, decoded_pk, num_nonces);
```

Add a test analogous to `test_gas_key_fee_parity` that uses `KeyType::MLDSA65` and asserts `add_a_outcome.gas_burnt == add_b_outcome.gas_burnt` across the transaction and host-function paths.

---

### Proof of Concept

**Divergent values (exact bytes):**

| Path | `public_key_len` passed to `gas_key_add_key_exec_fee` | Source |
|---|---|---|
| Transaction (`permission_exec_fees`) | `33` (`trie_id_len()` for ML-DSA-65) | `runtime/runtime/src/config.rs:392` |
| Host function (`promise_batch_action_add_gas_key_with_full_access`) | `1953` (`len()` wire format for ML-DSA-65) | `runtime/near-vm-runner/src/logic/logic.rs:3158` |

**Fee formula** (`core/parameters/src/cost.rs:888–896`):

```
nonce_key_len = access_key_key_len(account_id_len, public_key_len) + 2
per_byte_exec  = gas_key_byte_exec * (nonce_key_len + NONCE_VALUE_LEN) * num_nonces
```

With `account_id_len = 10` (e.g. `"alice.near"`), `num_nonces = 4`:
- Correct path: `nonce_key_len = 1 + 10 + 1 + 33 + 2 = 47`
- Wrong path: `nonce_key_len = 1 + 10 + 1 + 1953 + 2 = 1967`
- Ratio: `1967 / 47 ≈ 41.9×` overcharge on the per-byte exec component. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** runtime/runtime/src/config.rs (L389-394)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
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

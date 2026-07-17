### Title
`derive_near_deterministic_account_id` Truncates Keccak256 to 160 Bits, Enabling Meet-in-the-Middle Account-ID Collision to Pre-empt Legitimate Deterministic Account Deployments — (`core/primitives/src/utils.rs`)

---

### Summary

`derive_near_deterministic_account_id` produces a deterministic account ID by taking only the last 20 bytes (`[12..32]`) of a 256-bit Keccak256 digest over a fully user-controlled Borsh blob. This 160-bit truncation reduces the birthday-attack collision threshold from 2^128 to 2^80 operations. An unprivileged attacker who finds a collision between two distinct `DeterministicAccountStateInit` values that map to the same `0s…` account ID can pre-deploy a malicious global contract to that address, permanently blocking the legitimate `DeterministicStateInitAction` from ever initialising the account with its intended code and state.

---

### Finding Description

**Root cause — 160-bit truncation**

`derive_near_deterministic_account_id` in `core/primitives/src/utils.rs` computes:

```
account_id = "0s" + hex( keccak256( borsh(state_init) )[12..32] )
``` [1](#0-0) 

Only 20 bytes (160 bits) of the 32-byte digest are retained. The same truncation is present in the in-contract helper used by sharded contracts: [2](#0-1) 

**Fully user-controlled input**

`DeterministicAccountStateInitV1` contains two user-controlled fields:

- `code: GlobalContractIdentifier` — either a `CodeHash(CryptoHash)` (any 32-byte value the attacker chooses) or an `AccountId` (any account the attacker controls).
- `data: BTreeMap<Vec<u8>, Vec<u8>>` — arbitrary key-value pairs with no semantic constraint beyond per-entry size limits. [3](#0-2) 

Both fields feed directly into the Borsh blob that is hashed. The attacker has unrestricted freedom to vary them.

**Validation only checks self-consistency, not uniqueness**

`validate_deterministic_state_init` verifies that `derive_near_deterministic_account_id(action.state_init) == receiver_id`. It does not and cannot verify that no other `state_init` produces the same `receiver_id`. [4](#0-3) 

**Idempotency makes the first writer win permanently**

`action_deterministic_state_init` is a no-op when the account already has a contract (`account.contract().is_none()` is false). Once the attacker's state init is deployed, the legitimate deployment can never overwrite it. [5](#0-4) 

---

### Impact Explanation

An attacker who finds a collision between `state_init_EVIL` and `state_init_LEGIT` (both mapping to the same `0sCOLLIDED` account ID) can:

1. Deploy a malicious global contract (permissionless).
2. Construct `state_init_EVIL` referencing that contract and varying `data` until `derive_near_deterministic_account_id(state_init_EVIL) == 0sCOLLIDED`.
3. Submit a `DeterministicStateInitAction` with `state_init_EVIL` to `0sCOLLIDED` before the legitimate protocol does.
4. The account at `0sCOLLIDED` is now permanently initialised with the attacker's code and initial state.
5. Any subsequent `DeterministicStateInitAction` with `state_init_LEGIT` targeting `0sCOLLIDED` is silently accepted but does nothing.

Any protocol that relies on the deterministic-account guarantee — "the account at this ID runs exactly this code with exactly this initial state" — is broken. Funds sent to `0sCOLLIDED` expecting the legitimate contract's behaviour are instead handled by the attacker's contract.

---

### Likelihood Explanation

The birthday-attack collision probability over a 160-bit space reaches ~86 % at 2^81 hashes and ~99.96 % at 2^82 hashes. The Bitcoin network sustained ≈ 6 × 10^20 hashes/second at the time of the referenced Kyber audit (2023), implying 2^80 hashes in roughly 30 minutes. A fraction of that hashrate is sufficient. The attacker controls both sides of the meet-in-the-middle search (varying `data` on each side), so the effective search cost is 2 × 2^80 evaluations of `borsh + keccak256`, which is cheaper per operation than SHA-256 mining. The cost is a one-time investment; a single collision suffices to permanently corrupt one target account.

---

### Recommendation

Replace the 160-bit truncation with the full 256-bit digest. The codebase already contains `core/primitives-core/src/universal_account_id.rs`, which encodes a 32-byte hash as a `0u…` account ID (52 Crockford-base32 symbols + 6-symbol Bech32m checksum, 60 characters total — within the 64-character account-ID limit). [6](#0-5) 

Switching `derive_near_deterministic_account_id` to use `encode_universal_account_id` over the full 32-byte keccak256 digest raises the collision threshold to 2^128, making the attack computationally infeasible. Additionally, prepend a domain-separation tag (analogous to `HashDomainTag::MlDsa65PubkeyV1` used for ML-DSA-65 key handles) before hashing to prevent cross-context collisions. [7](#0-6) 

---

### Proof of Concept

**Step 1 — Collision search (off-chain)**

```
for i in 0..2^80:
    state_init_A = DeterministicAccountStateInit::V1 {
        code: GlobalContractIdentifier::CodeHash(ATTACKER_CONTRACT_HASH),
        data: { b"k" => i.to_le_bytes() }
    }
    addr_A = keccak256(borsh(state_init_A))[12..32]
    store addr_A in Bloom filter

for j in 0..2^80:
    state_init_B = DeterministicAccountStateInit::V1 {
        code: GlobalContractIdentifier::AccountId("legit.global"),
        data: { b"owner" => LEGIT_OWNER, b"nonce" => j.to_le_bytes() }
    }
    addr_B = keccak256(borsh(state_init_B))[12..32]
    if addr_B in Bloom filter:
        COLLISION FOUND: state_init_A and state_init_B both map to "0s" + hex(addr_B)
```

**Step 2 — Pre-empt the legitimate deployment (on-chain)**

```
// Attacker submits before the legitimate protocol:
SignedTransaction::deterministic_state_init(
    nonce,
    attacker_account,
    "0s<collided>",   // receiver_id == derive(state_init_A)
    &attacker_signer,
    block_hash,
    state_init_A,     // malicious code + data
    deposit,
)
```

**Step 3 — Legitimate deployment is silently swallowed**

```
// Legitimate protocol submits later:
SignedTransaction::deterministic_state_init(
    nonce,
    legit_account,
    "0s<collided>",   // same receiver_id
    &legit_signer,
    block_hash,
    state_init_B,     // intended code + data
    deposit,
)
// validate_deterministic_state_init passes (derive(state_init_B) == receiver_id)
// action_deterministic_state_init: account.contract().is_some() → no-op
// Account permanently holds attacker's code.
```

### Citations

**File:** core/primitives/src/utils.rs (L470-477)
```rust
pub fn derive_near_deterministic_account_id(
    state_init: &DeterministicAccountStateInit,
) -> AccountId {
    use sha3::Digest;
    let data = borsh::to_vec(&state_init).expect("borsh must not fail");
    let hash = sha3::Keccak256::digest(&data);
    format!("0s{}", hex::encode(&hash[12..32])).parse().unwrap()
}
```

**File:** runtime/near-test-contracts/sharded-contract/src/lib.rs (L283-292)
```rust
unsafe fn derive_near_deterministic_account_id(
    state_init: &DeterministicAccountStateInit,
) -> String {
    let data = borsh::to_vec(&state_init).expect("borsh must not fail");
    keccak256(data.len() as u64, data.as_ptr() as u64, REG_A);
    let hash = register_to_memory(REG_A);

    let hex_string: String = hash[12..32].iter().map(|b| format!("{:02x}", b)).collect();
    format!("0s{hex_string}")
}
```

**File:** core/primitives-core/src/deterministic_account_id.rs (L39-44)
```rust
pub struct DeterministicAccountStateInitV1 {
    pub code: GlobalContractIdentifier,
    #[serde_as(as = "BTreeMap<Base64, Base64>")]
    #[cfg_attr(feature = "schemars", schemars(with = "BTreeMap<String, String>"))]
    pub data: BTreeMap<Vec<u8>, Vec<u8>>,
}
```

**File:** runtime/runtime/src/action_validation.rs (L413-427)
```rust
fn validate_deterministic_state_init(
    limit_config: &LimitConfig,
    action: &DeterministicStateInitAction,
    receiver_id: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_global_contract_identifier(action.state_init.code())?;

    let derived_id = derive_near_deterministic_account_id(&action.state_init);

    if derived_id != *receiver_id {
        return Err(ActionsValidationError::InvalidDeterministicStateInitReceiver {
            derived_id,
            receiver_id: receiver_id.clone(),
        });
    }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L38-48)
```rust
    if account.contract().is_none() {
        // `uninit` -> `active` account state transition
        deploy_deterministic_account(
            state_update,
            account,
            account_id,
            &action.state_init,
            result,
            storage_usage_config,
        )?;
    }
```

**File:** core/primitives-core/src/universal_account_id.rs (L1-35)
```rust
//! Codec for `0u` universal account ids (UAIDs).
//!
//! A UAID encodes a 32-byte hash as
//! `0u` + 52 Crockford-base32 symbols of the hash + a 6-symbol Bech32m BCH checksum
//! = 60 characters, all lowercase `[0-9a-z]`, which is a valid NEAR account id.
//!
//! This is a pure codec: it turns a hash into an address and back and validates
//! the checksum. Hashing a `StateInit` into the 32-byte input lives with the
//! account-id derivation, not here.
//!
//! The base32 and checksum are implemented in this module rather than taken from
//! an external crate. No existing crate fits the whole codec: the `bech32` crates
//! implement full Bech32/Bech32m framing and their own alphabet, which we don't
//! use, and a base32 crate would cover only the encoding half while the checksum
//! still lives here, so it would add a dependency to this foundational crate
//! without shrinking what we maintain. Keeping both halves together also avoids a
//! glyph-to-value pass, since they share one 5-bit symbol pipeline. The
//! implementation was cross-checked against the `data-encoding` crate over 40M
//! random cases with zero correctness divergence and on-par performance.
//!
//! The checksum reuses the Bech32m polymod from
//! [BIP-350](https://github.com/bitcoin/bips/blob/master/bip-0350.mediawiki).

// cspell:words bech polymod crockford uaid nbits kats multibyte

use crate::types::AccountId;

/// Scheme + hash-function marker. A different hash function gets a different letter.
pub const UAID_PREFIX: &str = "0u";
/// Base32 symbols encoding the 256-bit hash (`ceil(256 / 5)`).
pub const UAID_DATA_SYMBOLS: usize = 52;
/// Checksum symbols.
pub const UAID_CHECKSUM_SYMBOLS: usize = 6;
/// Total UAID length: prefix + data + checksum.
pub const UAID_LEN: usize = UAID_PREFIX.len() + UAID_DATA_SYMBOLS + UAID_CHECKSUM_SYMBOLS;
```

**File:** core/crypto/src/hash_domain.rs (L1-18)
```rust
/// Domain-separation tags for hashing in the protocol. The tag is prepended to
/// the input bytes before hashing, so a digest from one domain can never
/// collide with a digest from another. Each variant is one domain; uses that
/// are meant to produce the same digest must use the same variant.
#[derive(Debug, Clone, Copy)]
pub enum HashDomainTag {
    /// `MlDsa65PublicKey`-to-digest derivation. The digest serves as the
    /// on-trie access-key identifier (`MlDsa65PublicKeyHandle`).
    MlDsa65PubkeyV1,
}

impl HashDomainTag {
    pub const fn as_bytes(self) -> &'static [u8] {
        match self {
            HashDomainTag::MlDsa65PubkeyV1 => b"near:ml-dsa-65-pubkey-hash:v1",
        }
    }
}
```

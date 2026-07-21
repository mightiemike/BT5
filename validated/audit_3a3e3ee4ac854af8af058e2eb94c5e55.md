### Title
`PatriciaKey` derived `Deserialize` bypasses range invariant, accepting out-of-range `ContractAddress` from JSON — (`crates/starknet_api/src/core.rs`)

---

### Summary

`PatriciaKey` uses a derived `#[derive(Deserialize)]` with no `#[serde(try_from = "...")]` guard. The range check that enforces the invariant `key < 2**251` lives only in `PatriciaKey::try_from`, which serde's derived newtype deserializer never calls. Because `StarkHash` is a plain type alias for `Felt` and `2**251` is a valid field element (P = 2^251 + 17·2^192 + 1 > 2^251), a JSON value of `"0x800000000000000000000000000000000000000000000000000000000000000"` is accepted without error, producing a `ContractAddress` that violates the documented invariant.

---

### Finding Description

`StarkHash` is defined as `pub type StarkHash = Felt` in `crates/starknet_api/src/hash.rs`. [1](#0-0) 

`PatriciaKey` is a newtype over `StarkHash` with a plain `#[derive(Deserialize)]` and no `#[serde(try_from = "...")]` attribute: [2](#0-1) 

The range guard exists only in `TryFrom<StarkHash> for PatriciaKey`: [3](#0-2) 

Serde's derived `Deserialize` for a newtype struct calls `<StarkHash as Deserialize>::deserialize(deserializer)` and wraps the result directly in `PatriciaKey(...)`. It does **not** call `PatriciaKey::try_from`. The `try_from` path is therefore entirely bypassed during JSON deserialization.

`ContractAddress` also uses a plain `#[derive(Deserialize)]` that delegates to `PatriciaKey`'s derived deserializer, inheriting the same bypass: [4](#0-3) 

`CONTRACT_ADDRESS_DOMAIN_SIZE` equals `PATRICIA_KEY_UPPER_BOUND_FELT` = 2^251: [5](#0-4) [6](#0-5) 

The comment on `PatriciaKey` explicitly states the invariant that is violated: [7](#0-6) 

The existing unit test `patricia_key_out_of_range` only tests `try_from`, not the serde path, so the bypass is untested: [8](#0-7) 

---

### Impact Explanation

Any RPC or gateway endpoint that deserializes a `ContractAddress` from JSON (e.g., `starknet_call`, `starknet_getStorageAt`, `starknet_getClassAt`, transaction sender/recipient fields) will accept the value `0x800000000000000000000000000000000000000000000000000000000000000` without error. This value is then used as a Patricia trie key. The Patricia trie is defined over 251-bit keys; a key of exactly 2^251 is one bit too wide and maps to a node index outside the valid trie address space, producing a wrong storage key, wrong state value read or written, or undefined trie behavior — matching the Critical impact category: **wrong state, storage value, or revert result from execution logic for accepted input**.

---

### Likelihood Explanation

The path is fully unprivileged: any caller of a public JSON-RPC endpoint can supply the value. No operator, admin, or migration privilege is required. The exact divergent value (`0x800000000000000000000000000000000000000000000000000000000000000`) is concrete and trivially constructable.

---

### Recommendation

Add `#[serde(try_from = "StarkHash")]` to `PatriciaKey` and implement `TryFrom<StarkHash> for PatriciaKey` (already present) so that serde routes all deserialization through the range-checked constructor:

```rust
#[derive(..., Deserialize, ...)]
#[serde(try_from = "StarkHash")]
pub struct PatriciaKey(StarkHash);
```

This makes the derived `Deserialize` call `PatriciaKey::try_from(value)`, which already returns `Err(StarknetApiError::OutOfRange)` for any value ≥ 2^251. [3](#0-2) 

---

### Proof of Concept

```rust
#[test]
fn contract_address_deserialize_rejects_upper_bound() {
    // 0x800...0 == 2**251 == PATRICIA_KEY_UPPER_BOUND_FELT
    let json = r#""0x800000000000000000000000000000000000000000000000000000000000000""#;
    let result = serde_json::from_str::<ContractAddress>(json);
    // With the current derived Deserialize this SUCCEEDS (no error),
    // violating the invariant "key is in range [0, 2**251)".
    assert!(result.is_err(), "expected deserialization to fail for out-of-range key");
}
```

Under the current code the assertion fails — `result` is `Ok(...)` — confirming the invariant bypass.

### Citations

**File:** crates/starknet_api/src/hash.rs (L13-13)
```rust
pub type StarkHash = Felt;
```

**File:** crates/starknet_api/src/core.rs (L251-267)
```rust
#[derive(
    Debug,
    Default,
    Copy,
    Clone,
    derive_more::Display,
    Eq,
    PartialEq,
    Hash,
    Deserialize,
    Serialize,
    PartialOrd,
    Ord,
    derive_more::Deref,
    SizeOf,
)]
pub struct ContractAddress(pub PatriciaKey);
```

**File:** crates/starknet_api/src/core.rs (L312-312)
```rust
pub const CONTRACT_ADDRESS_DOMAIN_SIZE: Felt = PATRICIA_KEY_UPPER_BOUND_FELT;
```

**File:** crates/starknet_api/src/core.rs (L546-547)
```rust
/// A key for nodes of a Patricia tree.
// Invariant: key is in range.
```

**File:** crates/starknet_api/src/core.rs (L548-564)
```rust
#[derive(
    Copy,
    Clone,
    derive_more::Display,
    Eq,
    PartialEq,
    Default,
    Hash,
    Deserialize,
    Serialize,
    PartialOrd,
    Ord,
    derive_more:: Deref,
    SizeOf,
)]
#[display("{}", _0.to_fixed_hex_string())]
pub struct PatriciaKey(StarkHash);
```

**File:** crates/starknet_api/src/core.rs (L566-569)
```rust
// 2**251
pub const PATRICIA_KEY_UPPER_BOUND: &str =
    "0x800000000000000000000000000000000000000000000000000000000000000";
pub const PATRICIA_KEY_UPPER_BOUND_FELT: Felt = Felt::from_hex_unchecked(PATRICIA_KEY_UPPER_BOUND);
```

**File:** crates/starknet_api/src/core.rs (L598-607)
```rust
impl TryFrom<StarkHash> for PatriciaKey {
    type Error = StarknetApiError;

    fn try_from(value: StarkHash) -> Result<Self, Self::Error> {
        if value < CONTRACT_ADDRESS_DOMAIN_SIZE {
            return Ok(PatriciaKey(value));
        }
        Err(StarknetApiError::OutOfRange { string: format!("[0x0, {PATRICIA_KEY_UPPER_BOUND})") })
    }
}
```

**File:** crates/starknet_api/src/core_test.rs (L36-42)
```rust
#[test]
fn patricia_key_out_of_range() {
    // 2**251
    let hash = felt!("0x800000000000000000000000000000000000000000000000000000000000000");
    let err = PatriciaKey::try_from(hash);
    assert_matches!(err, Err(StarknetApiError::OutOfRange { string: _err_str }));
}
```

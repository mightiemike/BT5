### Title
P2P Class Sync Breaks for Deprecated Classes with `abi: None` — (`crates/apollo_protobuf/src/converters/class.rs`)

---

### Summary

The `From<ContractClass> for protobuf::Cairo0Class` conversion encodes `abi: None` as an empty string `""`. The inverse `TryFrom<protobuf::Cairo0Class> for ContractClass` calls `serde_json::from_str("")`, which returns `Err` because `""` is not valid JSON. The round-trip is irreversibly broken: any deprecated class with `abi: None` that is sent over P2P cannot be deserialized by the receiving node, causing that class to be permanently absent from the receiver's state.

---

### Finding Description

**Serialization side** — `From<ContractClass> for protobuf::Cairo0Class`:

```rust
// crates/apollo_protobuf/src/converters/class.rs, line 148-159
let encoded_abi = match value.abi {
    Some(abi_entries) => { /* serialize to JSON string */ }
    None => "".to_string(),   // ← encodes None as empty string
};
``` [1](#0-0) 

**Deserialization side** — `TryFrom<protobuf::Cairo0Class> for ContractClass`:

```rust
// crates/apollo_protobuf/src/converters/class.rs, line 131
let abi = serde_json::from_str(&value.abi)?;
``` [2](#0-1) 

`serde_json::from_str::<Option<Vec<ContractClassAbiEntry>>>("")` returns `Err` — an empty string is not valid JSON. The `?` propagates the error, causing `TryFrom` to return `Err` for every class whose `abi` was `None`.

The correct JSON encoding for `None` would be `"null"` (or `"[]"` for an empty vec), not `""`.

**`abi: None` is a legitimate, reachable state.** The `ContractClass` struct explicitly documents this:

```rust
// crates/starknet_api/src/deprecated_contract_class.rs, line 19-21
// Starknet does not verify the abi. If we can't parse it, we set it to None.
#[serde(default, deserialize_with = "deserialize_optional_contract_class_abi_entry_vector")]
pub abi: Option<Vec<ContractClassAbiEntry>>,
``` [3](#0-2) 

The custom deserializer `deserialize_optional_contract_class_abi_entry_vector` silently converts unparseable ABI values to `None` — meaning any class declared with a non-standard or unparseable ABI will have `abi: None` in the node's internal representation. [4](#0-3) 

Note that this custom deserializer is **not** invoked by the protobuf `TryFrom` path — it calls `serde_json::from_str` directly, bypassing the lenient fallback.

The P2P class sync path flows through:

```
(ApiContractClass, ClassHash) → protobuf::Class → protobuf::Cairo0Class
                                                         ↓
                                          TryFrom fails → ProtobufConversionError
``` [5](#0-4) 

---

### Impact Explanation

A receiving node that attempts to sync a deprecated class with `abi: None` via P2P will fail to deserialize it. The class will be absent from the receiving node's state. Any transaction that invokes this class on the receiving node will fail to find the compiled class, causing the node to diverge from canonical chain state — matching the impact category: **wrong/missing compiled class selected for execution**.

---

### Likelihood Explanation

Any Cairo 0 contract declared with an ABI that the node cannot parse (which is explicitly handled by setting `abi: None`) will trigger this bug on every P2P sync peer. This is not a theoretical edge case — the code explicitly accommodates `abi: None` as a normal operating state.

---

### Recommendation

In `From<ContractClass> for protobuf::Cairo0Class`, encode `None` as `"null"` instead of `""`:

```rust
None => "null".to_string(),
```

And in `TryFrom<protobuf::Cairo0Class> for ContractClass`, handle the empty-string legacy case:

```rust
let abi = if value.abi.is_empty() {
    None
} else {
    serde_json::from_str(&value.abi)?
};
```

The existing test `convert_cairo_0_class_to_protobuf_and_back` does not cover `abi: None` because `GetTestInstance` likely generates a non-`None` ABI. [6](#0-5) 

---

### Proof of Concept

```rust
#[test]
fn abi_none_round_trip() {
    use starknet_api::deprecated_contract_class::ContractClass;
    use crate::protobuf::Cairo0Class;

    let original = ContractClass { abi: None, ..Default::default() };
    let proto: Cairo0Class = original.clone().into();
    assert_eq!(proto.abi, "");  // encodes as empty string

    // This panics: serde_json::from_str("") returns Err
    let recovered: ContractClass = proto.try_into().unwrap();
    assert_eq!(recovered.abi, None);
}
```

### Citations

**File:** crates/apollo_protobuf/src/converters/class.rs (L59-78)
```rust
impl TryFrom<protobuf::Class> for (ApiContractClass, ClassHash) {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::Class) -> Result<Self, Self::Error> {
        let class = match value.class {
            Some(protobuf::class::Class::Cairo0(class)) => {
                ApiContractClass::DeprecatedContractClass(
                    deprecated_contract_class::ContractClass::try_from(class)?,
                )
            }
            Some(protobuf::class::Class::Cairo1(class)) => {
                ApiContractClass::ContractClass(state::SierraContractClass::try_from(class)?)
            }
            None => {
                return Err(missing("Class::class"));
            }
        };
        let class_hash =
            value.class_hash.ok_or(missing("Class::class_hash"))?.try_into().map(ClassHash)?;
        Ok((class, class_hash))
    }
```

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-131)
```rust
        let abi = serde_json::from_str(&value.abi)?;
```

**File:** crates/apollo_protobuf/src/converters/class.rs (L148-159)
```rust
        let encoded_abi = match value.abi {
            Some(abi_entries) => {
                let mut abi_bytes = vec![];
                abi_entries
                    .serialize(&mut serde_json::Serializer::with_formatter(
                        &mut abi_bytes,
                        PythonJsonFormatter,
                    ))
                    .expect("ABI is not in the expected Pythonic JSON byte format");
                String::from_utf8(abi_bytes).expect("Failed decoding ABI bytes as utf8 string")
            }
            None => "".to_string(),
```

**File:** crates/starknet_api/src/deprecated_contract_class.rs (L19-21)
```rust
    // Starknet does not verify the abi. If we can't parse it, we set it to None.
    #[serde(default, deserialize_with = "deserialize_optional_contract_class_abi_entry_vector")]
    pub abi: Option<Vec<ContractClassAbiEntry>>,
```

**File:** crates/starknet_api/src/serde_utils.rs (L144-158)
```rust
pub fn deserialize_optional_contract_class_abi_entry_vector<'de, D>(
    deserializer: D,
) -> Result<Option<Vec<ContractClassAbiEntry>>, D::Error>
where
    D: Deserializer<'de>,
{
    // Deserialize the field as an `Option<Vec<ContractClassAbiEntry>>`
    let result: Result<Option<Vec<ContractClassAbiEntry>>, _> = Option::deserialize(deserializer);

    // If the field contains junk or an invalid value, return `None`.
    match result {
        Ok(value) => Ok(value),
        Err(_) => Ok(None),
    }
}
```

**File:** crates/apollo_protobuf/src/converters/class_test.rs (L6-11)
```rust
fn convert_cairo_0_class_to_protobuf_and_back() {
    let expected_cairo_0_class = ContractClass::get_test_instance(&mut get_rng());
    let protobuf_class: Cairo0Class = expected_cairo_0_class.clone().into();
    let cairo_0_class: ContractClass = protobuf_class.try_into().unwrap();
    assert_eq!(cairo_0_class, expected_cairo_0_class);
}
```

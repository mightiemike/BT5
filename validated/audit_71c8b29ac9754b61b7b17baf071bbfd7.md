The evidence is conclusive. Here is the full analysis:

---

### Title
Silent Drop of `AddMod`, `MulMod`, and `RangeCheck96` Builtin Counts in P2P Protobuf Receipt Deserialization — (`crates/apollo_protobuf/src/converters/receipt.rs`)

### Summary

The `BuiltinCounter` protobuf message in the P2P sync receipt schema has no fields for `add_mod`, `mul_mod`, or `range_check96`. The Rust converter `From<HashMap<Builtin, u64>> for ProtobufBuiltinCounter` silently discards those three counts on serialization, and `TryFrom<ProtobufBuiltinCounter> for HashMap<Builtin, u64>` never inserts them on deserialization. Any synced block containing a Cairo1 transaction that used these builtins will have its stored `ExecutionResources` permanently wrong, and the node will serve those wrong values authoritatively via RPC.

### Finding Description

**Proto schema — no fields for the three builtins:**

The `BuiltinCounter` message in `receipt.proto` defines only 8 fields (`bitwise`, `ecdsa`, `ec_op`, `pedersen`, `range_check`, `poseidon`, `keccak`, `output`) with an explicit `// TODO(alonl): add the missing builtins` comment. [1](#0-0) 

**Serialization path — three keys silently dropped:**

`From<HashMap<Builtin, u64>> for ProtobufBuiltinCounter` iterates only the 7 legacy builtins and hardcodes `output: 0`. `Builtin::AddMod`, `Builtin::MulMod`, and `Builtin::RangeCheck96` are never read from the map. [2](#0-1) 

**Deserialization path — three keys never inserted:**

`TryFrom<ProtobufBuiltinCounter> for HashMap<Builtin, u64>` inserts only the same 7 legacy builtins plus `SegmentArena: 0`. `AddMod`, `MulMod`, and `RangeCheck96` are absent from the resulting map. [3](#0-2) 

**Domain type — all three variants exist:**

`starknet_api::execution_resources::Builtin` fully defines `AddMod`, `MulMod`, and `RangeCheck96` as first-class variants, so the domain model is correct; only the wire format is incomplete. [4](#0-3) 

**Storage serializer — all three variants are present:**

The storage-layer serializer assigns distinct discriminants (`AddMod = 8`, `MulMod = 9`, `RangeCheck96 = 10`), confirming these builtins are expected to survive the full pipeline. [5](#0-4) 

### Impact Explanation

A node that syncs blocks via P2P will deserialize receipts through this path. Any transaction in a block that used `add_mod`, `mul_mod`, or `range_check96` builtins (Cairo1 contracts, Starknet ≥ 0.13.2) will have those counts silently zeroed in the stored `ExecutionResources`. The node then serves those wrong values authoritatively through `starknet_getTransactionReceipt` and `starknet_traceTransaction`. Clients relying on execution resource data for fee modeling, bouncer analysis, or debugging receive a structurally plausible but factually wrong response with no error signal.

This maps to: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

- Any Cairo1 contract using `u256` arithmetic, modular arithmetic libraries, or range-checked 96-bit operations will exercise these builtins.
- Starknet mainnet has had contracts using these builtins since the 0.13.2 upgrade.
- The path is triggered automatically during normal P2P block sync — no attacker action is required; a legitimate peer sending a correct receipt is sufficient.
- The `TODO` comment in the proto file confirms the gap is known but unresolved.

### Recommendation

1. Add `add_mod`, `mul_mod`, and `range_check96` fields to the `BuiltinCounter` proto message in `receipt.proto`.
2. Update `From<HashMap<Builtin, u64>> for ProtobufBuiltinCounter` to write those three fields.
3. Update `TryFrom<ProtobufBuiltinCounter> for HashMap<Builtin, u64>` to read and insert those three fields.
4. Add a round-trip serialization test that encodes `ExecutionResources` with `AddMod=10`, `MulMod=5`, `RangeCheck96=20`, serializes to protobuf, deserializes back, and asserts all three counts are preserved.

### Proof of Concept

```rust
// Encode ExecutionResources with AddMod=10, MulMod=5, RangeCheck96=20
let mut counter = HashMap::new();
counter.insert(Builtin::AddMod, 10u64);
counter.insert(Builtin::MulMod, 5u64);
counter.insert(Builtin::RangeCheck96, 20u64);

let resources = ExecutionResources {
    steps: 100,
    builtin_instance_counter: counter,
    memory_holes: 0,
    gas_consumed: GasVector::default(),
    da_gas_consumed: GasVector::default(),
};

// Serialize → protobuf
let proto: protobuf::receipt::ExecutionResources = resources.into();

// Deserialize ← protobuf
let recovered = ExecutionResources::try_from(proto).unwrap();

// All three are absent (effectively 0) — the bug
assert_eq!(recovered.builtin_instance_counter.get(&Builtin::AddMod).copied().unwrap_or(0), 10);     // FAILS: got 0
assert_eq!(recovered.builtin_instance_counter.get(&Builtin::MulMod).copied().unwrap_or(0), 5);      // FAILS: got 0
assert_eq!(recovered.builtin_instance_counter.get(&Builtin::RangeCheck96).copied().unwrap_or(0), 20); // FAILS: got 0
```

### Citations

**File:** crates/apollo_protobuf/src/proto/p2p/proto/sync/receipt.proto (L21-31)
```text
    message BuiltinCounter {
      uint32 bitwise = 1;
      uint32 ecdsa = 2;
      uint32 ec_op = 3;
      uint32 pedersen = 4;
      uint32 range_check = 5;
      uint32 poseidon = 6;
      uint32 keccak = 7;
      uint32 output = 8;
      // TODO(alonl): add the missing builtins
    }
```

**File:** crates/apollo_protobuf/src/converters/receipt.rs (L253-267)
```rust
impl TryFrom<ProtobufBuiltinCounter> for HashMap<Builtin, u64> {
    type Error = ProtobufConversionError;
    fn try_from(value: ProtobufBuiltinCounter) -> Result<Self, Self::Error> {
        let mut builtin_instance_counter = HashMap::new();
        builtin_instance_counter.insert(Builtin::RangeCheck, u64::from(value.range_check));
        builtin_instance_counter.insert(Builtin::Pedersen, u64::from(value.pedersen));
        builtin_instance_counter.insert(Builtin::Poseidon, u64::from(value.poseidon));
        builtin_instance_counter.insert(Builtin::EcOp, u64::from(value.ec_op));
        builtin_instance_counter.insert(Builtin::Ecdsa, u64::from(value.ecdsa));
        builtin_instance_counter.insert(Builtin::Bitwise, u64::from(value.bitwise));
        builtin_instance_counter.insert(Builtin::Keccak, u64::from(value.keccak));
        builtin_instance_counter.insert(Builtin::SegmentArena, 0);
        Ok(builtin_instance_counter)
    }
}
```

**File:** crates/apollo_protobuf/src/converters/receipt.rs (L269-291)
```rust
impl From<HashMap<Builtin, u64>> for ProtobufBuiltinCounter {
    fn from(value: HashMap<Builtin, u64>) -> Self {
        let builtin_counter = ProtobufBuiltinCounter {
            range_check: u32::try_from(*value.get(&Builtin::RangeCheck).unwrap_or(&0))
                // TODO(Shahak): should not panic
                .expect("Failed to convert u64 to u32"),
            pedersen: u32::try_from(*value.get(&Builtin::Pedersen).unwrap_or(&0))
                .expect("Failed to convert u64 to u32"),
            poseidon: u32::try_from(*value.get(&Builtin::Poseidon).unwrap_or(&0))
                .expect("Failed to convert u64 to u32"),
            ec_op: u32::try_from(*value.get(&Builtin::EcOp).unwrap_or(&0))
                .expect("Failed to convert u64 to u32"),
            ecdsa: u32::try_from(*value.get(&Builtin::Ecdsa).unwrap_or(&0))
                .expect("Failed to convert u64 to u32"),
            bitwise: u32::try_from(*value.get(&Builtin::Bitwise).unwrap_or(&0))
                .expect("Failed to convert u64 to u32"),
            keccak: u32::try_from(*value.get(&Builtin::Keccak).unwrap_or(&0))
                .expect("Failed to convert u64 to u32"),
            output: 0,
        };
        builtin_counter
    }
}
```

**File:** crates/starknet_api/src/execution_resources.rs (L257-263)
```rust
    #[serde(rename = "add_mod_builtin")]
    AddMod,
    #[serde(rename = "mul_mod_builtin")]
    MulMod,
    #[serde(rename = "range_check96_builtin")]
    RangeCheck96,
}
```

**File:** crates/apollo_storage/src/serialization/serializers.rs (L540-543)
```rust
        AddMod = 8,
        MulMod = 9,
        RangeCheck96 = 10,
    }
```

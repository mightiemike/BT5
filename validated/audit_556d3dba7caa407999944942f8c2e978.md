### Title
Protobuf `ValidResourceBounds` Deserializer Silently Collapses `AllResources{l2_gas=0, l1_data_gas=0}` to `L1Gas`, Producing a Different Transaction Hash Than the Submitting Gateway - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to distinguish old V3 transactions (`L1Gas` variant) from new V3 transactions (`AllResources` variant). When `l2_gas` and `l1_data_gas` are both zero, it silently downgrades `AllResources` to `L1Gas`. Because `get_tip_resource_bounds_hash` produces structurally different Poseidon hashes for these two variants (3-element vs 4-element input), any node that deserializes such a transaction via the consensus/state-sync protobuf path recomputes a different transaction hash than the gateway that originally accepted and hashed it. This is the sequencer analog of the Syndicate `=` vs `+=` bug: a lazy, value-based classification replaces the correct structural accumulation, causing the wrong preimage to be committed.

---

### Finding Description

**Step 1 — RPC submission always uses `AllResources`.**

`RpcDeclareTransactionV3` and `RpcInvokeTransactionV3` store resource bounds as `AllResourceBounds` (never `ValidResourceBounds::L1Gas`): [1](#0-0) 

A user may legitimately submit with `l2_gas=0` and `l1_data_gas=0` (e.g., a legacy-style V3 invoke that only pays L1 gas). The gateway wraps this as `ValidResourceBounds::AllResources(AllResourceBounds{l1_gas: X, l2_gas: 0, l1_data_gas: 0})` and computes hash **H₁** via `get_tip_resource_bounds_hash`. [2](#0-1) 

**Step 2 — `get_tip_resource_bounds_hash` produces structurally different hashes per variant.**

For `L1Gas`: `poseidon(tip, L1_GAS_packed, L2_GAS_packed)` — **3 elements**.
For `AllResources`: `poseidon(tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed)` — **4 elements**. [3](#0-2) 

Even when `l1_data_gas` is zero, the packed felt `get_concat_resource(&ResourceBounds::default(), L1_DATA_GAS)` is a non-zero felt (the resource name bits are non-zero), so the two Poseidon outputs are distinct. [4](#0-3) 

**Step 3 — The protobuf deserializer collapses `AllResources{l2=0, l1_data=0}` → `L1Gas`.**

When the transaction is propagated via the consensus/state-sync protobuf path, `DeclareTransactionV3Common` is deserialized using `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: [5](#0-4) 

The condition at line 431 fires: `l1_data_gas.is_zero() && l2_gas.is_zero()` → returns `ValidResourceBounds::L1Gas(l1_gas)`. The receiving node now holds `L1Gas(X)` instead of `AllResources{l1_gas: X, l2_gas: 0, l1_data_gas: 0}`. [6](#0-5) 

**Step 4 — The receiving node recomputes hash H₂ ≠ H₁.**

When the receiving node recomputes the transaction hash (e.g., for block validation or storage), it calls `get_tip_resource_bounds_hash` with `L1Gas(X)`, producing a 3-element Poseidon hash H₂. The original gateway computed H₁ with the 4-element `AllResources` path. H₁ ≠ H₂.

This is confirmed by the `DeclareTransactionV3Common` deserialization path: [7](#0-6) 

The `resource_bounds` field is deserialized via `ValidResourceBounds::try_from(...)`, which applies the lossy heuristic.

**Step 5 — The OS Cairo hash function always hashes 3 resource bounds (always `AllResources` semantics).**

The OS `hash_fee_fields` asserts `n_resource_bounds = 3` unconditionally: [8](#0-7) 

This means the OS always uses the 4-element preimage (tip + L1 + L2 + L1_DATA). A transaction whose Rust-side hash was computed with the 3-element `L1Gas` path (after the downgrade) will not match the OS-computed hash, causing the OS to reject the transaction or produce a wrong block hash.

---

### Impact Explanation

A V3 transaction submitted via RPC with `AllResourceBounds{l1_gas: X, l2_gas: 0, l1_data_gas: 0}` receives hash H₁ at the gateway. After protobuf round-trip through the consensus/state-sync path, the receiving node holds `L1Gas(X)` and recomputes hash H₂ ≠ H₁. This causes:

1. **Wrong transaction hash stored by syncing nodes** — the transaction is indexed under H₂, not H₁. Lookups by H₁ fail.
2. **OS hash mismatch** — the OS always uses the 4-element preimage; a node that re-executes the block using the downgraded `L1Gas` representation will compute a different fee-fields hash, producing a wrong block hash or rejecting the block.
3. **Consensus divergence** — proposer and validator nodes may disagree on the transaction hash, causing consensus failures for any block containing such a transaction.

This matches: **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

Any user submitting a V3 transaction via RPC with zero `l2_gas` and `l1_data_gas` (a valid and natural configuration for users who only want to pay L1 gas) triggers this path. No special privileges are required. The condition is reachable on every node that receives blocks via P2P state sync.

---

### Recommendation

The protobuf deserializer must not use a value-based heuristic to distinguish `L1Gas` from `AllResources`. Add an explicit discriminator field to `protobuf::ResourceBounds` (e.g., a boolean `is_all_resources` or an enum tag), and use it during deserialization instead of inspecting whether `l2_gas` and `l1_data_gas` are zero.

Until the protobuf schema is updated, the deserializer should default to `AllResources` when `l1_data_gas` is present (even if zero), reserving `L1Gas` only for the legacy case where `l1_data_gas` is absent (`None`):

```rust
// In TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // truly old 0.13.2 tx: no l1_data_gas field at all
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [9](#0-8) 

---

### Proof of Concept

```
1. Submit via RPC:
   InvokeV3 { resource_bounds: AllResourceBounds { l1_gas: {max_amount: 1000, max_price: 1},
                                                    l2_gas: {max_amount: 0, max_price: 0},
                                                    l1_data_gas: {max_amount: 0, max_price: 0} },
              ... }

2. Gateway computes H₁ = poseidon("invoke", version, sender, 
       poseidon(tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0), pack(L1_DATA,0,0)),  ← 4-element
       ...)

3. Transaction propagated via protobuf consensus path.
   Serialized: ResourceBounds { l1_gas: {1000,1}, l2_gas: {0,0}, l1_data_gas: {0,0} }

4. Receiver deserializes:
   l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → ValidResourceBounds::L1Gas({max_amount:1000, max_price:1})

5. Receiver computes H₂ = poseidon("invoke", version, sender,
       poseidon(tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0)),  ← 3-element, L1_DATA absent
       ...)

6. H₁ ≠ H₂  →  block rejected / wrong hash stored / OS rejects transaction.
```

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L352-366)
```rust
pub struct RpcDeclareTransactionV3 {
    // TODO(Mohammad): Check with Shahak why we need to keep the DeclareType.
    // pub r#type: DeclareType,
    pub sender_address: ContractAddress,
    pub compiled_class_hash: CompiledClassHash,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub contract_class: SierraContractClass,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L368-384)
```rust
impl From<RpcDeclareTransactionV3> for DeclareTransactionV3 {
    fn from(tx: RpcDeclareTransactionV3) -> Self {
        Self {
            class_hash: tx.contract_class.calculate_class_hash(),
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            compiled_class_hash: tx.compiled_class_hash,
            sender_address: tx.sender_address,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
        }
    }
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L188-211)
```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L216-226)
```rust
fn get_concat_resource(
    resource_bounds: &ResourceBounds,
    resource_name: &ResourceName,
) -> Result<Felt, StarknetApiError> {
    let max_amount = resource_bounds.max_amount.0.to_be_bytes();
    let max_price = resource_bounds.max_price_per_unit.0.to_be_bytes();
    let concat_bytes =
        [[0_u8].as_slice(), resource_name.as_slice(), max_amount.as_slice(), max_price.as_slice()]
            .concat();
    Ok(Felt::from_bytes_be(&concat_bytes.try_into().expect("Expect 32 bytes")))
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
```

**File:** crates/apollo_protobuf/src/transaction.rs (L35-96)
```rust
impl TryFrom<protobuf::DeclareV3Common> for DeclareTransactionV3Common {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeclareV3Common) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("DeclareV3Common::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("DeclareV3Common::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("DeclareV3Common::nonce"))?.try_into()?);

        let compiled_class_hash = CompiledClassHash(
            value
                .compiled_class_hash
                .ok_or(missing("DeclareV3Common::compiled_class_hash"))?
                .try_into()?,
        );

        let sender_address = value.sender.ok_or(missing("DeclareV3Common::sender"))?.try_into()?;

        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            compiled_class_hash,
            sender_address,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
        })
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L119-144)
```text
    static_assert L1_GAS_INDEX == 0;
    static_assert L2_GAS_INDEX == 1;
    static_assert L1_DATA_GAS_INDEX == 2;

    with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
        assert n_resource_bounds = 3;
    }

    // L1 gas.
    let l1_gas_bounds = resource_bounds[L1_GAS_INDEX];
    assert l1_gas_bounds.resource = L1_GAS;
    assert data_to_hash[1] = pack_resource_bounds(l1_gas_bounds);

    // L2 gas.
    let l2_gas_bounds = resource_bounds[L2_GAS_INDEX];
    assert l2_gas_bounds.resource = L2_GAS;
    assert data_to_hash[2] = pack_resource_bounds(l2_gas_bounds);

    // L1 data gas.
    let l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];
    assert l1_data_gas_bounds.resource = L1_DATA_GAS;
    assert data_to_hash[3] = pack_resource_bounds(l1_data_gas_bounds);

    let (hash) = poseidon_hash_many(n=n_resource_bounds + 1, elements=data_to_hash);
    return hash;
}
```

### Title
`ValidResourceBounds::AllResources` with zero L2/L1DataGas collapses to `L1Gas` in protobuf deserialization, producing a divergent transaction hash - (File: crates/apollo_protobuf/src/converters/transaction.rs)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion silently collapses an `AllResources` variant (with zero `l2_gas` and `l1_data_gas`) into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` produces structurally different hash preimages for these two variants, any V3 transaction submitted with `AllResources` and zero L2/L1DataGas bounds will carry a hash computed over three resource-bound felts, but after a protobuf round-trip (P2P block sync) the same transaction body will produce a hash computed over only two felts. The stored hash and the re-derived hash diverge, breaking the canonicalization invariant that a transaction's hash is stable across serialization boundaries.

### Finding Description

**Lossy protobuf deserialization** in `crates/apollo_protobuf/src/converters/transaction.rs`:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← type is changed
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

**Hash function produces structurally different outputs** for the two variants in `crates/starknet_api/src/transaction_hash.rs`:

```rust
// L1Gas  → 2-element poseidon: [tip, L1_GAS_concat, L2_GAS_concat(zeros)]
// AllResources → 3-element poseidon: [tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [2](#0-1) 

**Gateway always creates `AllResources`**: `InternalRpcInvokeTransactionV3.resource_bounds` is typed `AllResourceBounds`, so every transaction entering the sequencer is hashed with the three-element preimage. [3](#0-2) 

**Conversion path that triggers the collapse**: `InvokeTransactionV3` (used in block sync) carries `ValidResourceBounds`. When serialized to `protobuf::InvokeV3` and deserialized back, the `TryFrom` above fires. If `l2_gas == 0 && l1_data_gas == 0`, the variant is silently changed from `AllResources` to `L1Gas`. [4](#0-3) 

The hash stored at sequencing time (three-element poseidon) therefore diverges from the hash re-derived by any peer that receives the block over P2P (two-element poseidon).

### Impact Explanation

Any peer that re-derives and validates transaction hashes after P2P block deserialization will compute a different hash than the one committed by the sequencer. This breaks the "High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload" invariant: the same transaction body is bound to two different canonical hashes depending on which side of the protobuf boundary it is on. Concretely:

- A block containing such a transaction cannot be verified by a syncing node that re-hashes transactions from their deserialized form.
- The signature committed by the user (over the three-element hash) will not verify against the two-element hash re-derived after deserialization, causing signature-based block validation to fail.

### Likelihood Explanation

The trigger is unprivileged: any user may submit an `InvokeV3` transaction with `l2_gas = 0` and `l1_data_gas = 0` (only `l1_gas` non-zero). The gateway's stateless validator accepts such a transaction (it only requires at least one non-zero bound). The transaction will be reverted during execution (insufficient L2 gas), but reverted transactions are still included in blocks. Once included, the block cannot propagate correctly over P2P to any peer that re-derives hashes.

### Recommendation

Remove the type-collapsing heuristic from the protobuf deserializer. When all three resource-bound fields are present in the protobuf message, always produce `ValidResourceBounds::AllResources`, regardless of whether `l2_gas` and `l1_data_gas` are zero. The `L1Gas` variant should only be produced when deserializing legacy pre-0.13.3 messages that genuinely lack the `l1_data_gas` field (i.e., when `value.l1_data_gas` is `None` and `l2_gas` is zero).

### Proof of Concept

```
1. Submit InvokeV3 with AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }.
2. Gateway computes hash H1 = poseidon(INVOKE, version, sender, tip_resource_hash_3elem, ...).
   tip_resource_hash_3elem = poseidon(tip, L1_GAS_concat, L2_GAS_concat(0), L1_DATA_GAS_concat(0))
3. Transaction is included in block (reverted, but present).
4. Block is serialized to protobuf for P2P propagation.
5. Receiving peer deserializes: l1_data_gas.is_zero() && l2_gas.is_zero() → L1Gas variant.
6. Peer computes hash H2 = poseidon(INVOKE, version, sender, tip_resource_hash_2elem, ...).
   tip_resource_hash_2elem = poseidon(tip, L1_GAS_concat, L2_GAS_concat(0))
7. H1 ≠ H2 → hash validation fails → block rejected.
```

### Citations

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

**File:** crates/starknet_api/src/transaction_hash.rs (L203-208)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L616-628)
```rust
pub struct InternalRpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub proof_facts: ProofFacts,
}
```

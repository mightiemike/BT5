### Title
`ValidResourceBounds` Protobuf Type-Collapse Produces Divergent Transaction Hash for V3 Transactions with Zero L2/L1-Data Gas - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The protobuf deserializer for `ValidResourceBounds` silently collapses an `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` value into the legacy `L1Gas(X)` variant. Because `get_tip_resource_bounds_hash` includes `L1_DATA_GAS` in the Poseidon preimage only for the `AllResources` variant, the same logical transaction produces two distinct hashes depending on which deserialization path is taken. A valid V3 transaction accepted by the gateway with hash H1 arrives at a peer with hash H2 ≠ H1, causing the peer to reject it or commit it under the wrong hash.

### Finding Description

**Type-collapse in `ValidResourceBounds::try_from`** [1](#0-0) 

When `l1_data_gas` is absent (old 0.13.2 wire format) it is silently defaulted to zero, and then the branch `if l1_data_gas.is_zero() && l2_gas.is_zero()` promotes the result to `ValidResourceBounds::L1Gas(l1_gas)`. A new V3 transaction that legitimately carries `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is indistinguishable from an old L1-only transaction at this point.

**Hash function is variant-sensitive** [2](#0-1) 

`get_tip_resource_bounds_hash` appends the `L1_DATA_GAS` resource felt to the Poseidon chain **only** for `AllResources`. For `L1Gas` it is omitted entirely. Therefore:

- `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` → hash over `[tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA_GAS,0)]` → **H1**
- `L1Gas(X)` → hash over `[tip, concat(L1_GAS,X), concat(L2_GAS,0)]` → **H2 ≠ H1**

**Gateway always uses `AllResources`**

`InternalRpcInvokeTransactionV3.resource_bounds` is typed `AllResourceBounds` (never `ValidResourceBounds`), so `InvokeTransactionV3Trait::resource_bounds()` always returns `ValidResourceBounds::AllResources(...)`. [3](#0-2) 

The gateway therefore always computes H1 for such a transaction.

**P2P path goes through `InvokeTransactionV3` (uses `ValidResourceBounds::try_from`)**

The consensus/block-sync protobuf path deserializes via `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`, which calls `ValidResourceBounds::try_from` and collapses to `L1Gas`. [4](#0-3) 

A receiving peer that reconstructs the transaction through this path and recomputes the hash (as done in `convert_rpc_tx_to_internal`) obtains H2.

**Conversion failure on the receiving side**

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` explicitly rejects the `L1Gas` variant: [5](#0-4) 

So the receiving node either rejects the transaction outright with `DEPRECATED_RESOURCE_BOUNDS_ERROR`, or—if it reaches hash computation—stores it under H2 ≠ H1.

**Valid transaction trigger is reachable**

The stateless validator explicitly accepts `AllResourceBounds { l1_gas: NON_EMPTY, l2_gas: 0, l1_data_gas: 0 }` as a valid transaction: [6](#0-5) 

### Impact Explanation

A valid V3 invoke transaction with only L1 gas set (l2_gas = 0, l1_data_gas = 0) is accepted by the gateway and assigned hash H1. When propagated to peers via the consensus or block-sync P2P path, the `ValidResourceBounds::try_from` collapse converts it to `L1Gas`, causing either an outright rejection (`DEPRECATED_RESOURCE_BOUNDS_ERROR`) or a hash recomputation yielding H2 ≠ H1. In the latter case the peer stores the transaction under the wrong hash, breaking signature verification (the account signed H1) and causing consensus disagreement.

**Impact class:** High — valid transactions are rejected before sequencing; transaction hash/signature binding is broken across the public-to-internal conversion boundary.

### Likelihood Explanation

Any user submitting a V3 transaction with only L1 gas bounds (a common pattern for pre-0.13.3 compatibility or fee-minimization) triggers this path. No special privileges are required. The trigger is a normal RPC submission.

### Recommendation

1. **Remove the type-collapse heuristic** from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Instead, use a version-tagged wire field or a separate proto message to distinguish legacy L1-only transactions from new AllResources transactions with zero L2/data gas.
2. **Alternatively**, add a `TryFrom<protobuf::InvokeV3> for RpcInvokeTransactionV3` that uses `AllResourceBounds::try_from` directly (which already exists and does not collapse), bypassing `InvokeTransactionV3` for the mempool/consensus path.
3. **Add a canonicalization invariant test** asserting that `hash(AllResources{l1:X, l2:0, ld:0}) == hash(AllResources{l1:X, l2:0, ld:0})` after a protobuf round-trip.

### Proof of Concept

```
1. Construct RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds { l1_gas: {max_amount:1, max_price:1}, l2_gas: default(), l1_data_gas: default() }
     (all other fields: valid defaults, chain_id = SN_MAIN)

2. Submit to gateway → gateway calls convert_rpc_tx_to_internal:
     InvokeTransactionV3Trait::resource_bounds() returns ValidResourceBounds::AllResources(...)
     get_tip_resource_bounds_hash includes L1_DATA_GAS(0) → hash H1

3. Serialize to protobuf::InvokeV3:
     resource_bounds = ResourceBounds { l1_gas: {1,1}, l2_gas: {0,0}, l1_data_gas: {0,0} }

4. Deserialize via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas({1,1})

5. Attempt TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3:
     → Err(OutOfRange { string: "resource_bounds" })   ← transaction rejected

   OR if hash is recomputed with L1Gas variant:
     get_tip_resource_bounds_hash omits L1_DATA_GAS → hash H2 ≠ H1
     account signature (over H1) fails verification against H2
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-600)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);
```

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L589-598)
```rust
    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

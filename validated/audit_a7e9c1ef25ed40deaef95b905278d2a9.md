### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Coerces `AllResources(X, 0, 0)` to `L1Gas(X)`, Producing a Different Transaction Hash Preimage — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses a zero-value heuristic to reconstruct the enum variant. An `AllResources` transaction whose L2-gas and L1-data-gas bounds are both zero is silently re-typed as `L1Gas` after any protobuf round-trip. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element only for `AllResources`, the two variants produce structurally different Poseidon hash preimages. A node that receives a committed `InvokeTransactionV3` via P2P state-sync stores the wrong variant, causing the RPC to return an authoritative-looking but incorrect `resource_bounds` map and causing any hash recomputation (re-execution, simulation, fee estimation) to diverge from the on-chain hash.

---

### Finding Description

**Lossy protobuf deserialization of `ValidResourceBounds`**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // ← defaults to zero
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)                      // ← wrong for AllResources(X,0,0)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
``` [1](#0-0) 

The serialization side (`From<ValidResourceBounds> for protobuf::ResourceBounds`) emits `l1_data_gas = Some(zero)` for both `L1Gas` and `AllResources(X, 0, 0)`:

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),   // zero
    l1_data_gas: Some(ResourceBounds::default().into()),  // zero
},
``` [2](#0-1) 

Both variants produce the identical wire bytes `{l1_gas: X, l2_gas: 0, l1_data_gas: 0}`, so the deserializer cannot distinguish them and always picks `L1Gas`.

**Different hash preimage**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` conditionally appends the `L1_DATA_GAS` element:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements
    }
});
``` [3](#0-2) 

`AllResources(X, 0, 0)` hashes `[tip, L1_GAS(X), L2_GAS(0), L1_DATA_GAS(0)]` (3 resource felts). After protobuf round-trip the same transaction is typed as `L1Gas(X)` and hashes `[tip, L1_GAS(X), L2_GAS(0)]` (2 resource felts). The Poseidon outputs are different.

**Attack path**

1. Attacker submits `RpcInvokeTransactionV3` with `AllResourceBounds(l1_gas=X, l2_gas=0, l1_data_gas=0)` via the gateway.
2. `convert_rpc_tx_to_internal` converts it to `InternalRpcInvokeTransactionV3` (which carries `AllResourceBounds`) and calls `calculate_transaction_hash` → `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash` with `AllResources` → hash `H` includes the `L1_DATA_GAS(0)` element. [4](#0-3) 

3. The transaction is committed to a block as `InvokeTransactionV3` with `ValidResourceBounds::AllResources(X, 0, 0)`.
4. The block is propagated via P2P state-sync using `protobuf::InvokeV3`.
5. The receiving node calls `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`, which calls `ValidResourceBounds::try_from(...)` → `L1Gas(X)`. [5](#0-4) 

6. The receiving node stores `InvokeTransactionV3` with `ValidResourceBounds::L1Gas(X)` in MDBX.
7. Any subsequent call to `get_invoke_transaction_v3_hash` on the stored transaction produces hash `H'` ≠ `H`.
8. The RPC serializes `ValidResourceBounds::L1Gas` as `{L1_GAS: X, L2_GAS: 0}` (no `L1_DATA_GAS` key), diverging from the on-chain representation `{L1_GAS: X, L2_GAS: 0, L1_DATA_GAS: 0}`. [6](#0-5) 

---

### Impact Explanation

Any node that received the block via P2P state-sync (rather than executing it locally) stores the wrong `ValidResourceBounds` variant. Consequences:

- **RPC returns wrong `resource_bounds`**: `starknet_getTransactionByHash` / `starknet_getBlockWithTxs` omit the `L1_DATA_GAS` key, making the response structurally inconsistent with the on-chain transaction.
- **Hash divergence on re-execution / simulation**: `starknet_simulateTransactions`, `starknet_estimateFee`, and `blockifier_reexecution` all call `calculate_transaction_hash` on the stored `InvokeTransactionV3`. The recomputed hash `H'` differs from the committed hash `H`, causing re-execution to fail or to silently bind the wrong transaction identity.
- **`validate_transaction_hash` returns `false`**: The function in `transaction_hash.rs` recomputes the hash and checks it against the stored value; it will always fail for affected transactions on synced nodes. [7](#0-6) 

This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

- Any V3 invoke transaction with `l2_gas = 0` and `l1_data_gas = 0` triggers the bug. This is a normal, accepted configuration (zero bounds disable fee enforcement for those resources).
- The gateway's `StatelessTransactionValidator` imposes no minimum on `l2_gas` or `l1_data_gas`.
- The bug is triggered on every node that receives the block via P2P state-sync rather than building it locally, which is the majority of non-sequencer nodes.
- No privileged access is required; a standard RPC call suffices.

---

### Recommendation

**Short term**: In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, do not use the zero-value heuristic to infer the variant. Add an explicit discriminant field to the protobuf `ResourceBounds` message (e.g., `bool use_all_resources`) so the variant is preserved losslessly across the wire.

**Medium term**: Audit all other `ValidResourceBounds` deserialization paths (JSON `Deserialize`, `StorageSerde`) for the same coercion. The JSON `Deserialize` impl already handles this correctly (presence of `L1_DATA_GAS` key determines the variant), but the protobuf path does not.

---

### Proof of Concept

```
# 1. Submit a V3 invoke with AllResources(l1_gas=1000, l2_gas=0, l1_data_gas=0)
POST /gateway/add_transaction
{
  "type": "INVOKE",
  "version": "0x3",
  "resource_bounds": {
    "l1_gas":      {"max_amount": "0x3e8", "max_price_per_unit": "0x1"},
    "l2_gas":      {"max_amount": "0x0",   "max_price_per_unit": "0x0"},
    "l1_data_gas": {"max_amount": "0x0",   "max_price_per_unit": "0x0"}
  },
  ...
}

# 2. Gateway computes hash H using AllResources path (3 resource felts including L1_DATA_GAS(0))
#    H = Poseidon(INVOKE, 3, sender, Poseidon(tip, L1_GAS_felt, L2_GAS_felt, L1_DATA_GAS_felt), ...)

# 3. Transaction committed to block N.

# 4. A synced node receives block N via P2P state-sync.
#    protobuf::ResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }
#    → TryFrom → l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(1000)

# 5. Synced node stores InvokeTransactionV3 { resource_bounds: L1Gas(1000), ... }

# 6. starknet_getTransactionByHash on synced node returns:
#    "resource_bounds": {"L1_GAS": {...}, "L2_GAS": {...}}   ← missing L1_DATA_GAS

# 7. starknet_simulateTransactions on synced node recomputes hash H':
#    H' = Poseidon(INVOKE, 3, sender, Poseidon(tip, L1_GAS_felt, L2_GAS_felt), ...)
#    H' ≠ H  → simulation fails / returns wrong transaction hash
```

The divergence between `H` (3-element preimage, committed on-chain)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
```rust
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),
                l1_data_gas: Some(ResourceBounds::default().into()),
            },
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(l2_gas.into()),
                l1_data_gas: Some(l1_data_gas.into()),
            },
        }
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-660)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("InvokeV3::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("InvokeV3::nonce"))?.try_into()?);

        let sender_address = value.sender.ok_or(missing("InvokeV3::sender"))?.try_into()?;

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

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

        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L202-210)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L388-392)
```rust
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_api/src/transaction/fields.rs (L551-572)
```rust
impl Serialize for ValidResourceBounds {
    fn serialize<S>(&self, s: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let map = match self {
            ValidResourceBounds::L1Gas(l1_gas) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, ResourceBounds::default()),
            ]),
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, *l2_gas),
                (Resource::L1DataGas, *l1_data_gas),
            ]),
        };
        DeprecatedResourceBoundsMapping(map).serialize(s)
    }
```

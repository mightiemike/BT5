### Title
Lossy `ValidResourceBounds` Protobuf Round-Trip Silently Converts `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash and Execution Mode - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion used in the p2p sync path applies a value-based heuristic to reconstruct the `ValidResourceBounds` variant. When a V3 transaction carries `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`, the round-trip silently produces `L1Gas(X)`. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound felts for each variant (2 for `L1Gas`, 3 for `AllResources`), the transaction hash computed from the deserialized form diverges from the hash that was computed and signed at submission time. The same conversion also changes the `GasVectorComputationMode` from `All` to `NoL2Gas`, altering execution semantics for any re-execution (e.g., proof generation).

### Finding Description

**Root cause — value-based variant reconstruction without a canonical tag** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The protobuf wire format carries no explicit discriminant for the `ValidResourceBounds` variant. The deserializer infers the variant purely from whether the numeric values of `l2_gas` and `l1_data_gas` are zero. This is the direct analog of the FPIControllerPool bug: an implicit type conversion changes the semantic meaning of the data without any validation or error.

**Submission path always produces `AllResources`**

Every V3 transaction submitted through the gateway uses `RpcInvokeTransactionV3` / `RpcDeclareTransactionV3` / `RpcDeployAccountTransactionV3`, all of which carry `AllResourceBounds` directly. [2](#0-1) 

When converted to the storage type `InvokeTransactionV3`, the bounds are wrapped unconditionally as `ValidResourceBounds::AllResources(tx.resource_bounds)`: [3](#0-2) 

A user who submits a V3 transaction with `l2_gas = 0` and `l1_data_gas = 0` (both are valid zero values; no gateway check prevents them) causes the sequencer to store `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.

**Serialization preserves all three zero fields; deserialization collapses them**

The serializer emits all three fields regardless of variant: [4](#0-3) 

A peer that receives this protobuf message sees `l1_data_gas.is_zero() && l2_gas.is_zero() == true` and reconstructs `L1Gas(X)` — a different variant than what was originally stored.

**Hash divergence: different number of resource felts**

`get_tip_resource_bounds_hash` branches on the variant: [5](#0-4) 

- `L1Gas` → Poseidon over `[tip, l1_gas_packed, l2_gas_packed]` (3 elements)
- `AllResources` → Poseidon over `[tip, l1_gas_packed, l2_gas_packed, l1_data_gas_packed]` (4 elements)

Even though `l2_gas_packed` and `l1_data_gas_packed` are both zero, Poseidon is sensitive to the number of inputs. The two hashes are distinct. The transaction hash that was computed and stored at sequencing time (using `AllResources`) will not match any hash recomputed from the deserialized form (using `L1Gas`).

**Execution mode divergence**

`ValidResourceBounds::get_gas_vector_computation_mode` returns `All` for `AllResources` and `NoL2Gas` for `L1Gas`: [6](#0-5) 

`GasVectorComputationMode::All` causes the blockifier to enforce L2 gas bounds and track L2 gas consumption. `NoL2Gas` does not. For a transaction originally executed with `All` mode (zero L2 gas bound → immediate out-of-gas revert), re-execution under `NoL2Gas` mode uses `initial_gas_no_user_l2_bound()` (a large value) and may not revert: [7](#0-6) 

### Impact Explanation

Any component that recomputes the transaction hash from the deserialized `ValidResourceBounds` — including RPC tracing/simulation endpoints, the reexecution CLI, or the Starknet OS proof-generation path — will produce a hash that diverges from the canonical on-chain hash. This matches **High: Transaction conversion or signature/hash logic binds the wrong hash or executable payload**.

For syncing nodes that also perform proof generation, the execution mode divergence means a transaction that originally reverted (zero L2 gas bound, `All` mode) may succeed under re-execution (`NoL2Gas` mode), producing a wrong receipt and wrong state — matching **Critical: Wrong state, receipt, event, or revert result from blockifier/syscall/execution logic for accepted input**.

### Likelihood Explanation

The trigger is unprivileged: any user can submit a V3 transaction with `l2_gas = 0` and `l1_data_gas = 0`. No gateway check prevents this. The conversion fires automatically during p2p sync deserialization for every such transaction. The existing round-trip tests in `crates/apollo_protobuf/src/converters/rpc_transaction_test.rs` and `transaction_test.rs` use non-zero `l2_gas` and `l1_data_gas` values and do not cover this edge case: [8](#0-7) 

### Recommendation

- **Short term**: Remove the value-based heuristic. Add an explicit boolean or enum discriminant field to the `ResourceBounds` protobuf message (e.g., `bool is_all_resources`) so the variant can be reconstructed unambiguously. Until the wire format is updated, treat any protobuf message that originated from a V3 transaction (identifiable by context) as `AllResources` unconditionally.
- **Short term**: Add a round-trip test for `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` that asserts the deserialized variant equals `AllResources`, not `L1Gas`.
- **Long term**: Audit all `ValidResourceBounds` deserialization paths (protobuf, JSON-RPC `ResourceBoundsMapping`, `DeprecatedResourceBoundsMapping`) for the same value-collapse pattern.

### Proof of Concept

```
1. Submit a V3 invoke transaction with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds::default(),   // zero
         l1_data_gas: ResourceBounds::default(),   // zero
     }

2. Gateway stores: ValidResourceBounds::AllResources(AllResourceBounds { l1_gas: 1000/1, l2_gas: 0/0, l1_data_gas: 0/0 })

3. Hash at sequencing time (AllResources path):
     get_tip_resource_bounds_hash → Poseidon(tip, l1_packed, l2_packed=0, l1data_packed=0)
     → H_orig

4. Transaction committed; FullTransaction { tx, tx_hash: H_orig } sent over p2p sync.

5. Syncing peer deserializes ResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas(l1_gas)

6. Hash recomputed from deserialized form (L1Gas path):
     get_tip_resource_bounds_hash → Poseidon(tip, l1_packed, l2_packed=0)
     → H_deser  ≠  H_orig

7. Execution mode of deserialized tx: NoL2Gas (initial gas = initial_gas_no_user_l2_bound, large)
   Execution mode of original tx:     All     (initial gas = l2_gas.max_amount = 0 → immediate revert)

8. Re-execution of the deserialized transaction produces a different receipt (success vs. revert).
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-490)
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
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-566)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
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
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-584)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
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

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```

**File:** crates/blockifier/src/context.rs (L55-73)
```rust
    pub fn initial_sierra_gas(&self) -> GasAmount {
        match &self.tx_info {
            TransactionInfo::Deprecated(_)
            | TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::L1Gas(_),
                ..
            }) => self.block_context.versioned_constants.initial_gas_no_user_l2_bound(),
            TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds { l2_gas, .. }),
                ..
            }) => {
                #[cfg(feature = "reexecution")]
                if self.block_context.versioned_constants.ignore_user_l2_gas_bound {
                    return self.block_context.versioned_constants.initial_gas_no_user_l2_bound();
                }
                l2_gas.max_amount
            }
        }
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction_test.rs (L74-83)
```rust
lazy_static! {
    static ref RESOURCE_BOUNDS_MAPPING: AllResourceBounds = AllResourceBounds {
        l1_gas: ResourceBounds { max_amount: GasAmount(0x5), max_price_per_unit: GasPrice(0x6) },
        l2_gas: ResourceBounds { max_amount: GasAmount(0x6), max_price_per_unit: GasPrice(0x7) },
        l1_data_gas: ResourceBounds {
            max_amount: GasAmount(0x7),
            max_price_per_unit: GasPrice(0x8)
        },
    };
}
```

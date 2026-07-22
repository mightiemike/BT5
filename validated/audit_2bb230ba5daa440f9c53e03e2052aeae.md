### Title
Protobuf `ValidResourceBounds` round-trip silently downgrades `AllResources` to `L1Gas`, producing a divergent transaction hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently converts an `AllResources` transaction whose `l2_gas` and `l1_data_gas` are both zero into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound felts for each variant (3 elements for `L1Gas`, 4 elements for `AllResources`), the Poseidon hash of the fee-fields sub-tree changes after a protobuf round-trip. A transaction whose hash was computed under the `AllResources` path at submission time will produce a different hash when re-derived from the deserialized `L1Gas` representation during P2P state sync or block validation, breaking the canonicalization invariant that a transaction's hash must be stable across serialization boundaries.

### Finding Description

**Root cause — asymmetric protobuf deserialization**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` applies a lossy downgrade:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← 2-resource hash path
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The symmetric serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`) emits `l1_data_gas = 0` and `l2_gas = 0` for the `L1Gas` variant, so a round-trip of an `AllResources` value with those zero fields silently produces `L1Gas`. [2](#0-1) 

**Root cause — hash-domain divergence**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` hashes a different number of felts depending on the variant:

- `ValidResourceBounds::L1Gas` → `[tip, L1_packed, L2_packed(0)]` — **3 elements**
- `ValidResourceBounds::AllResources` → `[tip, L1_packed, L2_packed(0), L1_data_packed(0)]` — **4 elements**

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // ← 3 elements total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 4 elements total
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [3](#0-2) 

Poseidon is collision-resistant, so `hash([tip, L1, L2_zero]) ≠ hash([tip, L1, L2_zero, L1_data_zero])` for any non-trivial `tip` or `L1` value.

**Affected transaction type**

`DeployAccountTransactionV3` (the starknet-api type used in state sync and blockifier execution) carries `resource_bounds: ValidResourceBounds` and is serialized/deserialized via the protobuf path above. [4](#0-3) 

Its hash is computed via `get_deploy_account_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with the variant-dependent element count.

**Concrete divergence**

| Step | Variant | Hash input elements | Hash value |
|------|---------|---------------------|------------|
| Gateway submission | `AllResources(l2=0, l1_data=0)` | 4 | H₁ |
| After protobuf round-trip | `L1Gas` | 3 | H₂ ≠ H₁ |

The stored `tx_hash` in `InternalRpcTransaction` is H₁; any node that re-derives the hash from the deserialized `L1Gas` representation computes H₂ and observes a mismatch. [5](#0-4) 

### Impact Explanation

A deploy-account transaction submitted with `AllResourceBounds { l1_gas: non-zero, l2_gas: zero, l1_data_gas: zero }` is accepted by the gateway (the `max_possible_fee` check passes because `l1_gas` is non-zero). Its canonical hash H₁ is computed under the `AllResources` path. When this transaction is propagated to peers or replayed during state sync via the protobuf path, the `ValidResourceBounds` deserializer silently produces `L1Gas`, and any hash re-derivation yields H₂ ≠ H₁. This causes:

1. **Block validation failure on syncing nodes** — the re-derived transaction hash does not match the hash stored in the block, so the block is rejected.
2. **Wrong hash bound to executable payload** — the blockifier executes the transaction under a hash that differs from the one the account signed, violating the signature-domain invariant.

This matches the allowed impact: *"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

### Likelihood Explanation

The trigger requires a deploy-account transaction with `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}`. The gateway's `validate_resource_bounds` check rejects transactions where `l2_gas.max_price_per_unit < min_gas_price`. If `min_gas_price > 0` (the typical production setting), the gateway blocks the problematic input. However:

- Nodes configured with `validate_resource_bounds = false` or `min_gas_price = 0` accept such transactions.
- Historical transactions from before the `min_gas_price` check was introduced may already exist on-chain and would trigger the mismatch during re-execution or state sync.
- The protobuf path is also exercised by the P2P state-sync component independently of the gateway. [6](#0-5) 

### Recommendation

Fix the protobuf deserializer to preserve the `AllResources` variant unconditionally for V3 transactions, matching the behavior of `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` used in the RPC path:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let l1_gas: ResourceBounds = value.l1_gas.ok_or(...)?.try_into()?;
        let l2_gas: ResourceBounds = value.l2_gas.ok_or(...)?.try_into()?;
        let l1_data_gas: ResourceBounds = value.l1_data_gas.unwrap_or_default().try_into()?;
        // Always produce AllResources; never silently downgrade to L1Gas.
        Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
    }
}
```

Alternatively, align `get_tip_resource_bounds_hash` so that `AllResources` with zero L2/L1_data gas produces the same hash as `L1Gas` (i.e., omit the zero-valued L1_data_gas element for both variants when it is zero). The former fix is safer because it preserves the full signed payload.

### Proof of Concept

```
1. Craft a DeployAccountTransactionV3 with:
     l1_gas = { max_amount: 1000, max_price_per_unit: 500 }
     l2_gas = { max_amount: 0,    max_price_per_unit: 0   }
     l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }

2. Compute H₁ = get_deploy_account_transaction_v3_hash(tx)
   → get_tip_resource_bounds_hash with AllResources
   → Poseidon([tip, L1_packed, L2_packed(0), L1_data_packed(0)])   // 4 elements

3. Serialize tx to protobuf::DeployAccountV3 (resource_bounds has l2_gas=0, l1_data_gas=0).

4. Deserialize back via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → produces ValidResourceBounds::L1Gas(l1_gas)

5. Compute H₂ = get_deploy_account_transaction_v3_hash(deserialized_tx)
   → get_tip_resource_bounds_hash with L1Gas
   → Poseidon([tip, L1_packed, L2_packed(0)])                       // 3 elements

6. Assert H₁ ≠ H₂  ← hash mismatch; block validation fails on syncing node.
``` [7](#0-6) [8](#0-7)

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

**File:** crates/starknet_api/src/transaction.rs (L663-688)
```rust
/// An invoke V3 transaction.
#[derive(Debug, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct InvokeTransactionV3 {
    pub resource_bounds: ValidResourceBounds,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
}

impl TransactionHasher for InvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L143-147)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash, SizeOf)]
pub struct InternalRpcTransaction {
    pub tx: InternalRpcTransactionWithoutTxHash,
    pub tx_hash: TransactionHash,
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

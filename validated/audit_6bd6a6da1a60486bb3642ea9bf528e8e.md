### Title
Protobuf `ValidResourceBounds` deserialization silently downgrades `AllResources` to `L1Gas`, producing a divergent transaction hash domain — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently converts `AllResources { l2_gas: 0, l1_data_gas: 0 }` into `L1Gas`. Because `get_tip_resource_bounds_hash` includes `l1_data_gas` in the Poseidon preimage only for `AllResources`, the hash computed from the deserialized form differs from the hash that was computed and signed at submission time. Any component that recomputes the transaction hash from the deserialized resource bounds — including the Starknet OS during block execution — will produce a wrong hash, breaking proof validity or hash-based transaction identity.

---

### Finding Description

**Serialization path** (`ValidResourceBounds` → protobuf):

When `ValidResourceBounds::L1Gas` is serialized, a zero `l1_data_gas` field is injected:

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),
    l1_data_gas: Some(ResourceBounds::default().into()),  // zero injected
},
``` [1](#0-0) 

**Deserialization path** (protobuf → `ValidResourceBounds`):

The deserializer collapses any protobuf message where both `l2_gas` and `l1_data_gas` are zero back to `L1Gas`:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [2](#0-1) 

This means a transaction submitted as `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` — which is a valid gateway submission — is deserialized as `L1Gas { l1_gas: X }` after any P2P sync round-trip.

**Hash domain divergence** (`get_tip_resource_bounds_hash`):

The hash preimage differs by exactly one element depending on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // no l1_data_gas
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // l1_data_gas included
    }
});
``` [3](#0-2) 

Concretely:

| Variant | Poseidon preimage |
|---|---|
| `AllResources(l1=X, l2=0, l1d=0)` | `[tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA_GAS,0)]` |
| `L1Gas(l1=X)` | `[tip, concat(L1_GAS,X), concat(L2_GAS,0)]` |

These produce **different hashes** even though the numeric gas values are identical.

**Submission type constraint**: `RpcInvokeTransactionV3`, `RpcDeclareTransactionV3`, and `RpcDeployAccountTransactionV3` all carry `AllResourceBounds` (not `ValidResourceBounds`), so the gateway always computes the hash under the `AllResources` domain. [4](#0-3) 

The `InternalRpcTransactionWithoutTxHash::calculate_transaction_hash` call in the converter stores the `AllResources`-domain hash in `InternalRpcTransaction.tx_hash`. [5](#0-4) 

After a P2P sync round-trip, the stored `tx_hash` is the `AllResources` hash H₁, but the `resource_bounds` field in the deserialized transaction is `L1Gas`. Any component that recomputes the hash from the deserialized transaction — including `validate_transaction_hash`, the Starknet OS `compute_invoke_transaction_hash`, or any re-execution path — will compute H₂ ≠ H₁. [6](#0-5) 

The OS hash function enforces `n_resource_bounds = 3` for `AllResources` and iterates over all three bounds: [7](#0-6) 

If the OS receives the deserialized `L1Gas` form (2 bounds), it will either assert-fail or compute a 2-element hash, diverging from the block's stored transaction hash.

Additionally, the `GasVectorComputationMode` changes from `All` to `NoL2Gas` after the downgrade: [8](#0-7) 

This silently changes fee enforcement semantics for the deserialized transaction.

---

### Impact Explanation

A transaction submitted with `AllResources { l2_gas: 0, l1_data_gas: 0 }` is valid at the gateway and receives a canonical hash H₁ under the `AllResources` domain. After P2P sync deserialization, the same transaction carries `L1Gas` resource bounds. Any component that recomputes the hash from the deserialized transaction produces H₂ ≠ H₁. This breaks:

1. **Proof validity**: The Starknet OS recomputes transaction hashes during block execution. A mismatch between the OS-computed hash and the block's stored hash invalidates the proof.
2. **Hash-based transaction identity**: `validate_transaction_hash` returns `false` for the stored hash when called on the deserialized transaction.
3. **Fee enforcement domain**: The `GasVectorComputationMode` changes from `All` to `NoL2Gas`, altering which gas types are checked during pre-validation and post-execution.

This matches the impact category: *Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload* (High).

---

### Likelihood Explanation

- Any V3 transaction with `AllResources { l2_gas: 0, l1_data_gas: 0 }` triggers the bug. The gateway explicitly accepts `AllResourceBounds` with zero fields (the stateless validator only checks that at least one bound is non-zero in price, not amount).
- The conversion is automatic and unconditional in the P2P sync deserialization path.
- No special privileges are required; any unprivileged user can submit such a transaction.
- The TODO comment in the code (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) confirms this is a known transitional state, not an intentional permanent design. [9](#0-8) 

---

### Recommendation

Remove the `AllResources → L1Gas` downgrade in the protobuf deserializer. The variant should be preserved based on the original submission type, not inferred from whether numeric values happen to be zero. Specifically, change the deserialization to always produce `AllResources` when all three fields are present in the protobuf message (even if zero), and only produce `L1Gas` when the protobuf message genuinely lacks `l2_gas` and `l1_data_gas` fields (i.e., `value.l2_gas.is_none() && value.l1_data_gas.is_none()`). This preserves the hash domain invariant across serialization round-trips.

---

### Proof of Concept

```
1. User submits RpcInvokeTransactionV3 with:
       resource_bounds = AllResourceBounds {
           l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
           l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
           l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
       }

2. Gateway converts to InternalRpcInvokeTransactionV3 (resource_bounds: AllResourceBounds).
   Hash H₁ = Poseidon(tip, concat(L1_GAS,1000), concat(L2_GAS,0), concat(L1_DATA_GAS,0), ...)
   Stored in InternalRpcTransaction { tx_hash: H₁, tx: ... }.

3. Transaction included in block B. Block synced via P2P.

4. Receiving node deserializes via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
       l1_data_gas.is_zero() && l2_gas.is_zero()  →  ValidResourceBounds::L1Gas(l1_gas)

5. Receiving node recomputes hash from deserialized transaction:
   H₂ = Poseidon(tip, concat(L1_GAS,1000), concat(L2_GAS,0), ...)
   // l1_data_gas NOT included — only 2 resource elements

6. H₁ ≠ H₂.
   validate_transaction_hash(deserialized_tx, ..., H₁) → false.
   OS hash computation for deserialized_tx → H₂ ≠ block's stored H₁ → invalid proof.
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

**File:** crates/starknet_api/src/transaction_hash.rs (L165-185)
```rust
/// Validates the hash of a starknet transaction.
/// For transactions on testnet or those with a low block_number, we validate the
/// transaction hash against all potential historical hash computations. For recent
/// transactions on mainnet, the hash is validated by calculating the precise hash
/// based on the transaction version.
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L124-141)
```rust
    pub fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
    ) -> Result<TransactionHash, StarknetApiError> {
        let transaction_version = &self.version();
        match self {
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
        }
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L549-566)
```rust
/// An invoke account transaction that can be added to Starknet through the RPC.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L121-144)
```text
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

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```

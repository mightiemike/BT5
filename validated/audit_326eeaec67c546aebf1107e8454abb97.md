### Title
`ResourceBoundsMapping`→`ValidResourceBounds` Version-Detection Heuristic Produces Wrong Transaction Hash in RPC Simulation/Estimation vs. Gateway Sequencing Path - (`File: crates/apollo_rpc/src/v0_8/transaction.rs`)

### Summary

The RPC `estimate_fee` and `simulate_transactions` endpoints convert `BroadcastedTransaction` through `ResourceBoundsMapping` → `ValidResourceBounds`, applying a zero-value heuristic that classifies a V3 invoke with `l2_gas = 0` and `l1_data_gas = 0` as `ValidResourceBounds::L1Gas`. The gateway sequencing path receives the same transaction as `RpcInvokeTransactionV3` whose `resource_bounds` field is typed `AllResourceBounds`, which always maps to `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a different number of resource entries for the two variants (2 vs. 3), the transaction hash computed during simulation diverges from the hash computed during actual sequencing. Any contract that reads `get_tx_info().transaction_hash` will observe a different value in simulation than in execution, making `starknet_estimateFee` and `starknet_simulateTransactions` return authoritative-looking wrong values.

### Finding Description

**Root cause — the heuristic conversion:**

In `crates/apollo_rpc/src/v0_8/transaction.rs`, the `From<ResourceBoundsMapping> for ValidResourceBounds` impl classifies a mapping as the legacy `L1Gas` variant whenever both `l2_gas` and `l1_data_gas` are zero: [1](#0-0) 

**Gateway path — always `AllResources`:**

`RpcInvokeTransactionV3`, the type used by the gateway, hard-codes `resource_bounds: AllResourceBounds`, which always maps to `ValidResourceBounds::AllResources`: [2](#0-1) 

**Hash divergence — different number of resource entries:**

`get_tip_resource_bounds_hash` hashes only 2 resource entries for `L1Gas` (omits `L1_DATA_GAS`) but 3 entries for `AllResources`: [3](#0-2) 

For a V3 invoke with `l2_gas = 0, l1_data_gas = 0`:

| Path | Variant | `tip_resource_bounds_hash` inputs |
|---|---|---|
| `estimate_fee` / `simulate_transactions` | `L1Gas` | `[tip, L1_GAS_packed, L2_GAS_packed(0)]` |
| Gateway / sequencing | `AllResources` | `[tip, L1_GAS_packed, L2_GAS_packed(0), L1_DATA_GAS_packed(0)]` |

These produce different Poseidon hashes, so the full transaction hash differs between the two paths.

**Trigger point — RPC estimation/simulation:** [4](#0-3) [5](#0-4) 

Both `estimate_fee` and `simulate_transactions` call `tx.try_into()` on `BroadcastedTransaction`, which routes through the `ResourceBoundsMapping` → `ValidResourceBounds` heuristic.

### Impact Explanation

`starknet_estimateFee` and `starknet_simulateTransactions` return an authoritative-looking wrong value: the transaction hash embedded in the execution context (`get_tx_info().transaction_hash`) during simulation differs from the hash that will be assigned when the transaction is actually sequenced through the gateway. Any contract whose behavior depends on the transaction hash (e.g., replay-protection schemes, event emission keyed on tx hash, or hash-based commitments) will produce a divergent simulation trace. This matches the **High** impact: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

The trigger condition — a V3 invoke with `l2_gas.max_amount = 0, l2_gas.max_price_per_unit = 0, l1_data_gas.max_amount = 0, l1_data_gas.max_price_per_unit = 0` — is explicitly accepted by the gateway stateless validator (the test `valid_l1_gas` confirms a transaction with only `l1_gas` non-zero passes validation). Any unprivileged user can craft such a transaction and submit it to the public RPC endpoints. No special privileges or malformed bytes are required.

### Recommendation

1. **Unify the type used in `BroadcastedTransaction`**: Replace `ResourceBoundsMapping` with `AllResourceBounds` in the RPC invoke/declare/deploy-account V3 structs, mirroring `RpcInvokeTransactionV3`. This eliminates the heuristic entirely and ensures the simulation path always uses `AllResources`.

2. **If backward compatibility requires `ResourceBoundsMapping`**: Change the `From<ResourceBoundsMapping> for ValidResourceBounds` impl to always produce `AllResources` for V3 transactions, regardless of whether `l2_gas` and `l1_data_gas` are zero. The `L1Gas` variant should only be produced when deserializing pre-0.13.3 on-chain transactions (where `L1_DATA_GAS` key is absent from the map), not from a V3 broadcasted transaction that explicitly supplies all three fields as zero.

3. **Add a cross-path hash consistency test**: Assert that a V3 invoke with zero `l2_gas`/`l1_data_gas` produces the same transaction hash when converted via `BroadcastedTransaction::try_into()` as when converted via `RpcInvokeTransactionV3` → `InternalRpcTransactionWithoutTxHash::calculate_transaction_hash`.

### Proof of Concept

```
// Craft a V3 invoke with zero l2_gas and l1_data_gas
let broadcasted = BroadcastedTransaction::Invoke(InvokeTransactionV3 {
    resource_bounds: ResourceBoundsMapping {
        l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
        l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 }, // zero
        l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 }, // zero
    },
    sender_address: ACCOUNT,
    calldata: [...],
    nonce: N,
    ...
});

// Path A: RPC estimate_fee / simulate_transactions
// ResourceBoundsMapping { l2_gas=0, l1_data_gas=0 }
//   → ValidResourceBounds::L1Gas(l1_gas)          ← heuristic fires
//   → get_tip_resource_bounds_hash hashes [tip, L1_GAS, L2_GAS(0)]  (2 entries)
//   → tx_hash_A = Poseidon([INVOKE, ver, sender, H_A, ...])

// Path B: Gateway sequencing (same logical transaction)
// RpcInvokeTransactionV3 { resource_bounds: AllResourceBounds { l1_gas, l2_gas=0, l1_data_gas=0 } }
//   → ValidResourceBounds::AllResources(...)       ← always AllResources
//   → get_tip_resource_bounds_hash hashes [tip, L1_GAS, L2_GAS(0), L1_DATA_GAS(0)] (3 entries)
//   → tx_hash_B = Poseidon([INVOKE, ver, sender, H_B, ...])

// H_A ≠ H_B  →  tx_hash_A ≠ tx_hash_B
// starknet_simulateTransactions returns execution trace with tx_hash_A
// but the sequenced transaction will have tx_hash_B
// Any contract reading get_tx_info().transaction_hash sees the wrong value in simulation
```

### Citations

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-199)
```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else {
            Self::AllResources(AllResourceBounds {
                l1_gas: value.l1_gas,
                l1_data_gas: value.l1_data_gas,
                l2_gas: value.l2_gas,
            })
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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1018-1019)
```rust
        let executable_txns =
            transactions.into_iter().map(|tx| tx.try_into()).collect::<Result<_, _>>()?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1074-1075)
```rust
        let executable_txns =
            transactions.into_iter().map(|tx| tx.try_into()).collect::<Result<_, _>>()?;
```

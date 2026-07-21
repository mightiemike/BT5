### Title
V3 Transaction Hash Divergence via `ResourceBoundsMapping → ValidResourceBounds` Misclassification in RPC Simulation Path - (File: crates/apollo_rpc/src/v0_8/transaction.rs)

### Summary

The `From<ResourceBoundsMapping> for ValidResourceBounds` conversion in the RPC layer silently downgrades a V3 transaction to `ValidResourceBounds::L1Gas` whenever `l1_data_gas` and `l2_gas` are both zero. Because `get_tip_resource_bounds_hash` produces a structurally different Poseidon preimage for `L1Gas` (two resource entries) versus `AllResources` (three resource entries, including a zero `l1_data_gas` slot), the transaction hash computed during `starknet_simulateTransactions` / `starknet_estimateFee` diverges from the hash the gateway and blockifier compute for the same byte-identical transaction. The gateway always uses `AllResourceBounds` directly and never passes through this conversion, so the two paths are permanently out of sync for this input class.

### Finding Description

**Conversion boundary — `crates/apollo_rpc/src/v0_8/transaction.rs` lines 188–199:**

```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)          // ← wrong variant for a V3 tx
        } else {
            Self::AllResources(AllResourceBounds { … })
        }
    }
}
``` [1](#0-0) 

The condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is a value-level test, not a version-level test. A V3 `BroadcastedTransaction` with both fields zeroed (e.g., a client-side-proving invoke with `max_price_per_unit = 0` on all resources) satisfies the condition and is silently reclassified as the pre-0.13.3 `L1Gas` variant.

**Hash function — `crates/starknet_api/src/transaction_hash.rs` lines 188–211:**

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 entries
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 entries
    }
});
``` [2](#0-1) 

For identical numeric values (`l1_data_gas = 0`, `l2_gas = 0`), the two variants produce different Poseidon digests because the `L1Gas` path omits the `L1_DATA_GAS` slot entirely, while `AllResources` includes it as a zero-valued felt. The resulting hashes are irreconcilably different.

**Gateway path — always `AllResources`:**

`RpcInvokeTransactionV3`, `RpcDeclareTransactionV3`, and `RpcDeployAccountTransactionV3` all carry `resource_bounds: AllResourceBounds` and implement `InvokeTransactionV3Trait::resource_bounds()` by wrapping with `ValidResourceBounds::AllResources(self.resource_bounds)`. The gateway therefore always hashes with three resource entries. [3](#0-2) 

**RPC simulation path — may produce `L1Gas`:**

`starknet_simulateTransactions` and `starknet_estimateFee` accept `BroadcastedTransaction`, whose V3 variants carry `resource_bounds: ResourceBoundsMapping`. The conversion `ResourceBoundsMapping → ValidResourceBounds` is the only path between the wire format and the hash computation for these endpoints. [4](#0-3) 

The same misclassification exists in the protobuf P2P converter: [5](#0-4) 

### Impact Explanation

When a caller submits a V3 `BroadcastedInvokeTransaction` with `l1_data_gas = {0, 0}` and `l2_gas = {0, 0}` to `starknet_simulateTransactions`:

1. The RPC layer converts `ResourceBoundsMapping → ValidResourceBounds::L1Gas`.
2. `get_invoke_transaction_v3_hash` calls `get_tip_resource_bounds_hash` with the `L1Gas` variant, producing a two-entry Poseidon hash.
3. The simulation trace and any `get_execution_info()` syscall result inside the simulated execution expose this wrong hash.
4. The gateway, if the same transaction were submitted, would compute a three-entry Poseidon hash — a different value.

The simulation returns an authoritative-looking transaction hash that does not match the hash the sequencer will assign. Any contract logic that branches on `get_execution_info().transaction_hash` (e.g., replay-protection, cross-contract authentication) will observe a different value in simulation than in actual execution, making simulation results unreliable for this class of transaction.

This matches the allowed impact: **"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

### Likelihood Explanation

The trigger condition (`l1_data_gas.is_zero() && l2_gas.is_zero()`) is exactly the shape of client-side-proving invoke transactions, where all `max_price_per_unit` fields are required to be zero and `l1_data_gas.max_amount` is irrelevant to OS execution. The `starknet_transaction_prover` documentation explicitly instructs users to set all price fields to zero: [6](#0-5) 

Any user following this guidance and calling `starknet_simulate

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

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L219-233)
```rust
#[derive(Debug, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct DeclareTransactionV3 {
    pub resource_bounds: ResourceBoundsMapping,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub class_hash: ClassHash,
    pub compiled_class_hash: CompiledClassHash,
    pub sender_address: ContractAddress,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub version: TransactionVersion3,
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L392-434)
```rust
/// Validates resource bounds for proving, collecting all violations into a single error.
///
/// Since proving is client-side, no fees are charged. All `max_price_per_unit` fields and `tip`
/// must be zero. The `max_amount` fields have different semantics:
/// - `l2_gas.max_amount`: determines the gas limit the OS enforces on the transaction. Must be
///   non-zero. Set this to the value returned by `starknet_estimateFee`, or use a safe upper bound
///   like 100,000,000 (sufficient for ~1 million Cairo steps).
/// - `l1_gas.max_amount` and `l1_data_gas.max_amount`: do not affect OS execution and can be any
///   value.
fn validate_zero_fee_resource_bounds(
    tx: &RpcInvokeTransactionV3,
) -> Result<(), VirtualSnosProverError> {
    let bounds = &tx.resource_bounds;
    let mut violations = Vec::new();

    if bounds.l1_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l1_gas.max_price_per_unit = {}", bounds.l1_gas.max_price_per_unit.0));
    }
    if bounds.l2_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l2_gas.max_price_per_unit = {}", bounds.l2_gas.max_price_per_unit.0));
    }
    if bounds.l1_data_gas.max_price_per_unit != GasPrice(0) {
        violations.push(format!(
            "l1_data_gas.max_price_per_unit = {}",
            bounds.l1_data_gas.max_price_per_unit.0
        ));
    }
    if tx.tip != Tip(0) {
        violations.push(format!("tip = {}", tx.tip.0));
    }

    if !violations.is_empty() {
        return Err(VirtualSnosProverError::InvalidTransactionInput(format!(
            "Proving is client-side — no fees are charged. The following fields must be zero but \
             were not: [{}]. Set all max_price_per_unit fields and tip to 0x0. Note: max_amount \
             fields are fine to set — l2_gas.max_amount controls the gas limit enforced by the OS \
             (use the value from starknet_estimateFee, or 100000000 as a safe upper bound). \
             l1_gas.max_amount and l1_data_gas.max_amount do not affect OS execution.",
            violations.join(", ")
        )));
    }
```

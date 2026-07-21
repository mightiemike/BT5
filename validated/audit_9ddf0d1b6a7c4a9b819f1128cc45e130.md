### Title
`ResourceBoundsMapping` → `ValidResourceBounds` Conversion Silently Selects Wrong Hash Variant for V3 Transactions with Zero `l1_data_gas`/`l2_gas` — (`File: crates/apollo_rpc/src/v0_8/transaction.rs`)

---

### Summary

The `From<ResourceBoundsMapping> for ValidResourceBounds` conversion in the RPC v0.8 layer silently selects `ValidResourceBounds::L1Gas` (a 2-element resource-bounds hash) whenever `l1_data_gas == 0` and `l2_gas == 0`. New V3 transactions ingested through the gateway always use `ValidResourceBounds::AllResources` (a 3-element hash). When the same transaction is later re-presented to the simulation/estimation endpoint — as the RPC v0.8 layer serialises it back to `ResourceBoundsMapping` with those zero fields — the hash recomputed inside the simulation diverges from the canonical on-chain hash. The simulation endpoint returns an authoritative-looking but wrong transaction hash.

---

### Finding Description

**Ingestion path (always `AllResources`):**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` directly. [1](#0-0) 

The `From<RpcInvokeTransactionV3> for InvokeTransactionV3` conversion unconditionally wraps it in `ValidResourceBounds::AllResources`: [2](#0-1) 

`InternalRpcInvokeTransactionV3::resource_bounds()` also always returns `AllResources`: [3](#0-2) 

So the canonical hash is always computed with the 3-element Poseidon input `[tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed]`. [4](#0-3) 

**RPC v0.8 round-trip (can produce `L1Gas`):**

When the stored `InvokeTransactionV3` (with `ValidResourceBounds`) is serialised for the RPC v0.8 response, it is converted to `ResourceBoundsMapping`: [5](#0-4) 

If the original transaction had `l1_data_gas = 0` and `l2_gas = 0`, the resulting `ResourceBoundsMapping` has both fields at zero. When a client takes this response and submits it to `starknet_simulateTransactions` or `starknet_estimateFee`, the simulation layer converts `ResourceBoundsMapping` back to `ValidResourceBounds` using: [6](#0-5) 

The condition `value.l1_data_gas.is_zero() && value.l2_gas.is_zero()` is true, so the result is `ValidResourceBounds::L1Gas`. `get_tip_resource_bounds_hash` then produces a 2-element hash `[tip, L1_GAS_packed, L2_GAS_packed]` — omitting `L1_DATA_GAS_packed`: [7](#0-6) 

The two Poseidon hashes are structurally different inputs and therefore produce different digests. The simulation returns a hash that does not match the canonical on-chain hash.

---

### Impact Explanation

The simulation/estimation endpoint returns an authoritative-looking transaction hash that differs from the hash actually stored on-chain for any V3 invoke transaction whose `l1_data_gas` and `l2_gas` resource bounds are both zero. A client that relies on the simulated hash to pre-compute a transaction hash (e.g., for signing, tracking, or fee estimation) will receive a wrong value. This matches the allowed impact: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

V3 transactions that specify only L1 gas (setting `l1_data_gas = 0` and `l2_gas = 0`) are valid and common for simple transfers or calls that do not require L2 gas or blob data. Any such transaction re-submitted to the simulation endpoint via the RPC v0.8 path triggers the divergence. No special privileges are required; any unprivileged user can trigger this by calling `starknet_simulateTransactions` with a V3 transaction that has zero `l1_data_gas` and `l2_gas`.

---

### Recommendation

Remove the `L1Gas` branch from `From<ResourceBoundsMapping> for ValidResourceBounds` in the RPC v0.8 layer. Since all new V3 transactions are ingested as `AllResourceBounds`, the simulation layer should always reconstruct `ValidResourceBounds::AllResources`, preserving the 3-element hash invariant:

```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        // Always AllResources for V3 transactions; L1Gas is a legacy-only variant.
        Self::AllResources(AllResourceBounds {
            l1_gas: value.l1_gas,
            l1_data_gas: value.l1_data_gas,
            l2_gas: value.l2_gas,
        })
    }
}
```

Alternatively, add an explicit assertion in the simulation path that the reconstructed `ValidResourceBounds` variant matches the variant stored in the canonical transaction before computing the hash.

---

### Proof of Concept

1. Submit a V3 invoke transaction with `l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }` and `l2_gas = { max_amount: 0, max_price_per_unit: 0 }` via the gateway. The canonical hash `H_canonical` is computed using `get_tip_resource_bounds_hash` with `AllResources` → 4-element Poseidon input.

2. Retrieve the transaction via `starknet_getTransactionByHash`. The response contains `resource_bounds` as a `ResourceBoundsMapping` with `l1_data_gas = 0` and `l2_gas = 0`.

3. Submit the same transaction body to `starknet_simulateTransactions`. The RPC layer calls `From<ResourceBoundsMapping> for ValidResourceBounds` at `crates/apollo_rpc/src/v0_8/transaction.rs:190`, which returns `ValidResourceBounds::L1Gas`. `get_tip_resource_bounds_hash` is called with `L1Gas` → 3-element Poseidon input (no `L1_DATA_GAS_packed`).

4. The hash `H_simulated` returned by the simulation differs from `H_canonical`. Concretely, `H_canonical = Poseidon(tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed)` while `H_simulated = Poseidon(tip, L1_GAS_packed, L2_GAS_packed)`.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L551-566)
```rust
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L528-555)
```rust
            starknet_api::transaction::InvokeTransaction::V3(
                starknet_api::transaction::InvokeTransactionV3 {
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
                },
            ) => Ok(Self::Version3(InvokeTransactionV3 {
                sender_address,
                calldata,
                version: TransactionVersion3::Version3,
                signature,
                nonce,
                resource_bounds: resource_bounds.into(),
                tip,
                nonce_data_availability_mode,
                fee_data_availability_mode,
                paymaster_data,
                account_deployment_data,
                proof_facts,
            })),
```

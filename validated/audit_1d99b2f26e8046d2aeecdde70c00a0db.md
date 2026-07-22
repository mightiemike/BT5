### Title
`ResourceBoundsMapping`→`ValidResourceBounds` heuristic in RPC simulation path produces a different transaction hash than the gateway admission path for transactions with zero L2/L1-data gas bounds — (File: `crates/apollo_rpc/src/v0_8/transaction.rs`)

---

### Summary

The `From<ResourceBoundsMapping> for ValidResourceBounds` conversion in the RPC layer uses a zero-value heuristic to decide which hash domain to apply. The gateway admission path always binds the `AllResources` hash domain (3 resource felts: L1\_GAS + L2\_GAS + L1\_DATA\_GAS). The RPC simulation/estimation path can silently downgrade to the `L1Gas` hash domain (2 resource felts: L1\_GAS + L2\_GAS only) for the same transaction bytes when both `l2_gas` and `l1_data_gas` are zero. The two domains produce different Poseidon digests, so `starknet_simulateTransactions` and `starknet_estimateFee` return an authoritative-looking wrong result — specifically, a spurious `__validate__` failure — for any transaction that was correctly signed and accepted under the `AllResources` domain.

---

### Finding Description

**Conversion boundary — `crates/apollo_rpc/src/v0_8/transaction.rs`**

```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)          // ← 2-resource hash domain
        } else {
            Self::AllResources(AllResourceBounds { ... })  // ← 3-resource hash domain
        }
    }
}
``` [1](#0-0) 

**Gateway admission path** — `RpcInvokeTransactionV3.resource_bounds` is typed as `AllResourceBounds` (never `L1Gas`), so `InternalRpcInvokeTransactionV3::resource_bounds()` always returns `ValidResourceBounds::AllResources(...)`:

```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)   // always AllResources
    }
``` [2](#0-1) 

**RPC simulation/estimation path** — the broadcasted transaction is deserialized as `InvokeTransactionV3 { resource_bounds: ResourceBoundsMapping, ... }` and then converted:

```rust
InvokeTransaction::Version3(InvokeTransactionV3 { resource_bounds, ... }) =>
    Self::V3(starknet_api::transaction::InvokeTransactionV3 {
        resource_bounds: resource_bounds.into(),   // ← heuristic fires here
        ...
    })
``` [3](#0-2) 

**Hash function — `get_tip_resource_bounds_hash`** — the number of felts hashed differs by variant:

```rust
// For AllResources: hashes tip + L1_GAS + L2_GAS + L1_DATA_GAS  (3 resource felts)
// For L1Gas:        hashes tip + L1_GAS + L2_GAS                 (2 resource felts)
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [4](#0-3) 

Even when `l1_data_gas = ResourceBounds::default()` (all-zero), `get_concat_resource` encodes the `L1_DATA` resource name bytes into the felt, producing a non-zero felt that changes the Poseidon digest. The two hash chains therefore diverge for identical numeric field values.

---

### Impact Explanation

**Impact: High — RPC simulation/estimation returns an authoritative-looking wrong value.**

A user who:
1. Submits a valid `AllResourceBounds` transaction with `l2_gas = 0` and `l1_data_gas = 0` through the gateway (hash H₁, `AllResources` domain, signature valid),
2. Then calls `starknet_simulateTransactions` or `starknet_estimateFee` with the same transaction bytes,

will receive a simulation result where the account's `__validate__` entry point is called with hash H₂ (`L1Gas` domain, H₂ ≠ H₁). The account contract verifies the signature against the transaction hash; since the signature covers H₁, verification fails under H₂. The simulation returns a spurious validation failure for a transaction that is accepted and executes correctly on-chain.

The inverse is also possible: a transaction signed under the `L1Gas` domain (H₂) would pass simulation but fail on-chain, because the gateway always computes H₁.

---

### Likelihood Explanation

The trigger is unprivileged and requires only a standard transaction submission. Transactions with zero `l2_gas` and `l1_data_gas` bounds are explicitly supported — the client-side proving flow (`validate_zero_fee_resource_bounds`) explicitly permits zero `l1_gas.max_amount` and `l1_data_gas.max_amount`. Any wallet or tooling that submits such a transaction and then calls `starknet_estimateFee` or `starknet_simulateTransactions` to pre-flight it will observe the divergence.

---

### Recommendation

Remove the zero-value heuristic from `From<ResourceBoundsMapping> for ValidResourceBounds`. Since all post-0.13.3 transactions use the `AllResources` domain, the conversion should unconditionally produce `ValidResourceBounds::AllResources(...)` when all three resource fields are present in the mapping, regardless of whether their values are zero. The `L1Gas` variant should only be produced when deserializing pre-0.13.3 wire formats that structurally lack the `l1_data_gas` field.

Alternatively, align the simulation/estimation deserialization path to use the same `AllResourceBounds`-typed struct that the gateway uses (`RpcInvokeTransactionV3`), eliminating the conversion entirely.

---

### Proof of Concept

```
1. Construct AllResourceBounds { l1_gas: X, l2_gas: zero, l1_data_gas: zero }.
2. Compute H1 = get_invoke_transaction_v3_hash with ValidResourceBounds::AllResources(...)
   → hash chain includes L1_DATA_GAS felt (3 resource felts).
3. Sign H1 with the account key.
4. Submit via starknet_addInvokeTransaction → gateway accepts, stores with hash H1.
5. Submit the same fields to starknet_simulateTransactions as ResourceBoundsMapping
   { l1_gas: X, l2_gas: zero, l1_data_gas: zero }.
6. From<ResourceBoundsMapping> fires: l1_data_gas.is_zero() && l2_gas.is_zero() → L1Gas(X).
7. Simulation computes H2 = get_invoke_transaction_v3_hash with ValidResourceBounds::L1Gas(X)
   → hash chain omits L1_DATA_GAS felt (2 resource felts). H2 ≠ H1.
8. __validate__ receives H2; ECDSA verify(sig_over_H1, H2) → FAIL.
9. starknet_simulateTransactions returns validation failure for a transaction
   that executes successfully on-chain.
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

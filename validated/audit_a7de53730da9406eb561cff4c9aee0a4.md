### Title
DA Mode Silently Discarded in `BroadcastedDeclareV3Transaction` → `client_transaction::DeclareV3Transaction` Conversion — (`crates/apollo_rpc/src/v0_8/broadcasted_transaction.rs`)

---

### Summary

The `TryFrom<BroadcastedDeclareTransaction> for client_transaction::DeclareTransaction` conversion unconditionally hardcodes `ReservedDataAvailabilityMode::Reserved` (= 0) for both `nonce_data_availability_mode` and `fee_data_availability_mode`, silently discarding whatever the user supplied. A user who signs a V3 declare transaction with `DataAvailabilityMode::L2` (= 1) will have their transaction forwarded to the gateway with DA mode = 0. The gateway recomputes the hash using 0; the user's signature covers a hash computed with 1. Signature verification fails and the transaction is rejected.

---

### Finding Description

In `crates/apollo_rpc/src/v0_8/broadcasted_transaction.rs`, the `V3` arm of the `TryFrom` impl reads:

```rust
nonce_data_availability_mode:
    client_transaction::ReservedDataAvailabilityMode::Reserved,
fee_data_availability_mode:
    client_transaction::ReservedDataAvailabilityMode::Reserved,
``` [1](#0-0) 

The user-supplied `declare_v3.nonce_data_availability_mode` and `declare_v3.fee_data_availability_mode` are never read. `ReservedDataAvailabilityMode` is defined as:

```rust
#[repr(u8)]
pub enum ReservedDataAvailabilityMode {
    Reserved = 0,
}
``` [2](#0-1) 

The comment at the definition site explicitly acknowledges this is a protocol-version gate: *"the GW receives this field with value 0 as a reserved value. Once the feature will be activated this enum should be removed."* [3](#0-2) 

The `DeclareV3Transaction` gateway writer struct carries `nonce_data_availability_mode: ReservedDataAvailabilityMode`, so the serialized JSON sent to the gateway always encodes 0 for this field. [4](#0-3) 

The `BroadcastedDeclareTransaction` type (including the V3 variant with user-controlled DA modes) is consumed in the RPC API implementation: [5](#0-4) 

By contrast, the internal Apollo path (`RpcDeclareTransactionV3` → `InternalRpcDeclareTransactionV3`) correctly preserves the DA mode field end-to-end: [6](#0-5) 

---

### Impact Explanation

The Starknet V3 transaction hash preimage includes the DA mode fields. A user who constructs and signs a declare V3 transaction with `nonce_data_availability_mode = L2` (encoded as 1 in the hash) will have the RPC layer forward a transaction with DA mode = 0 to the gateway. The gateway computes the hash with 0; the signature covers the hash with 1. Signature verification fails unconditionally. The transaction is rejected despite being correctly formed and signed.

Impact category: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

---

### Likelihood Explanation

Any user on Starknet v0.13.x who submits a V3 declare transaction with `nonce_data_availability_mode: L2` via the `starknet_addDeclareTransaction` RPC endpoint will trigger this path. No special privileges are required. The divergence is deterministic (always produces 0 regardless of input).

---

### Recommendation

In the `V3` arm of `TryFrom<BroadcastedDeclareTransaction> for client_transaction::DeclareTransaction`, map the user-supplied DA mode to the appropriate `ReservedDataAvailabilityMode` variant (or reject the transaction if the protocol version does not yet support L2 DA mode). Do not silently substitute a hardcoded value. If the gateway truly only accepts `Reserved = 0` for the current protocol version, the conversion should return an explicit error when the user supplies any other value, rather than silently corrupting the field.

---

### Proof of Concept

```
1. Construct BroadcastedDeclareV3Transaction {
       nonce_data_availability_mode: DataAvailabilityMode::L2,  // = 1
       fee_data_availability_mode:   DataAvailabilityMode::L2,  // = 1
       ... (valid signature over hash computed with DA mode = 1)
   }

2. Call TryFrom<BroadcastedDeclareTransaction> for client_transaction::DeclareTransaction.

3. Observe: resulting DeclareV3Transaction.nonce_data_availability_mode
            == ReservedDataAvailabilityMode::Reserved  (= 0)
            ≠ DataAvailabilityMode::L2                 (= 1)

4. Gateway receives the transaction with DA mode = 0.
   Gateway recomputes hash with DA mode = 0.
   User's signature covers hash with DA mode = 1.
   Signature verification fails → transaction rejected.
``` [7](#0-6)

### Citations

**File:** crates/apollo_rpc/src/v0_8/broadcasted_transaction.rs (L164-192)
```rust
            BroadcastedDeclareTransaction::V3(declare_v3) => {
                Ok(Self::DeclareV3(client_transaction::DeclareV3Transaction {
                    contract_class: client_transaction::ContractClass {
                        compressed_sierra_program: compress_and_encode(
                            &declare_v3.contract_class.sierra_program,
                        )?,
                        contract_class_version: declare_v3.contract_class.contract_class_version,
                        entry_points_by_type: declare_v3
                            .contract_class
                            .entry_points_by_type
                            .to_hash_map(),
                        abi: declare_v3.contract_class.abi,
                    },
                    resource_bounds: declare_v3.resource_bounds.into(),
                    tip: declare_v3.tip,
                    signature: declare_v3.signature,
                    nonce: declare_v3.nonce,
                    compiled_class_hash: declare_v3.compiled_class_hash,
                    sender_address: declare_v3.sender_address,
                    nonce_data_availability_mode:
                        client_transaction::ReservedDataAvailabilityMode::Reserved,
                    fee_data_availability_mode:
                        client_transaction::ReservedDataAvailabilityMode::Reserved,
                    paymaster_data: declare_v3.paymaster_data,
                    account_deployment_data: declare_v3.account_deployment_data,
                    version: TransactionVersion::THREE,
                    r#type: client_transaction::DeclareType::Declare,
                }))
            }
```

**File:** crates/apollo_starknet_client/src/writer/objects/transaction.rs (L77-83)
```rust
// This enum is required since the GW receives this field with value 0 as a reserved value. Once the
// feature will be activated this enum should be removed from here and taken from starknet-api.
#[derive(Debug, Deserialize_repr, Serialize_repr, Clone, Eq, PartialEq)]
#[repr(u8)]
pub enum ReservedDataAvailabilityMode {
    Reserved = 0,
}
```

**File:** crates/apollo_starknet_client/src/writer/objects/transaction.rs (L238-252)
```rust
pub struct DeclareV3Transaction {
    pub contract_class: ContractClass,
    pub resource_bounds: DeprecatedResourceBoundsMapping,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub compiled_class_hash: CompiledClassHash,
    pub sender_address: ContractAddress,
    pub nonce_data_availability_mode: ReservedDataAvailabilityMode,
    pub fee_data_availability_mode: ReservedDataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub version: TransactionVersion,
    pub r#type: DeclareType,
}
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1-1)
```rust
use std::sync::Arc;
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L368-383)
```rust
impl From<RpcDeclareTransactionV3> for DeclareTransactionV3 {
    fn from(tx: RpcDeclareTransactionV3) -> Self {
        Self {
            class_hash: tx.contract_class.calculate_class_hash(),
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            compiled_class_hash: tx.compiled_class_hash,
            sender_address: tx.sender_address,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
        }
    }
```

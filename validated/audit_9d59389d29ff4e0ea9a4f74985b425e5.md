### Title
`paid_fee_on_l1` Silently Dropped at `ConsensusTransaction` Boundary and Hardcoded to `Fee(1)` on Reconstruction — (`File: crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

The `ConsensusTransaction::L1Handler` variant carries only `transaction::L1HandlerTransaction`, which has no `paid_fee_on_l1` field. When a proposer converts an `InternalConsensusTransaction::L1Handler` (which holds the real fee scraped from the L1 event) into a `ConsensusTransaction` for P2P broadcast, the `paid_fee_on_l1` is silently dropped. When any validator node receives that message and reconstructs the internal transaction, `convert_consensus_l1_handler_to_internal_l1_handler` unconditionally injects `Fee(1)` — an arbitrary placeholder — instead of the actual fee. The same substitution occurs in the RPC layer's `stored_txn_to_executable_txn`. Because the blockifier's only fee guard checks `paid_fee != Fee(0)`, `Fee(1)` always passes, so the wrong value propagates into the central blob and every validator's execution context without any error.

---

### Finding Description

**Step 1 — `paid_fee_on_l1` is stripped at the outbound conversion boundary.**

`convert_internal_consensus_tx_to_consensus_tx` reduces an `InternalConsensusTransaction::L1Handler` (which is `executable_transaction::L1HandlerTransaction` and carries `paid_fee_on_l1`) to `ConsensusTransaction::L1Handler(tx.tx)`, keeping only the inner `transaction::L1HandlerTransaction`:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:178-180
InternalConsensusTransaction::L1Handler(tx) => {
    Ok(ConsensusTransaction::L1Handler(tx.tx))   // paid_fee_on_l1 dropped here
}
```

The protobuf serialiser confirms the loss: `From<L1HandlerTransaction> for protobuf::L1HandlerV0` encodes only `nonce`, `address`, `entry_point_selector`, and `calldata` — `paid_fee_on_l1` is absent from the wire format entirely.

**Step 2 — Reconstruction hardcodes `Fee(1)`.**

`convert_consensus_tx_to_internal_consensus_tx` calls `convert_consensus_l1_handler_to_internal_l1_handler`, which passes the hardcoded constant `Fee(1)` as `paid_fee_on_l1`:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:473-483
fn convert_consensus_l1_handler_to_internal_l1_handler(
    &self,
    tx: transaction::L1HandlerTransaction,
) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
    Ok(executable_transaction::L1HandlerTransaction::create(
        tx,
        &self.chain_id,
        // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
        Fee(1),
    )?)
}
```

The same substitution exists in the RPC re-execution path:

```rust
// crates/apollo_rpc/src/v0_8/api/mod.rs:424-428
starknet_api::transaction::Transaction::L1Handler(value) => {
    // todo(yair): This is a temporary solution until we have a better way to get the l1 fee.
    let paid_fee_on_l1 = Fee(1);
    Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
}
```

**Step 3 — The blockifier's only guard is trivially bypassed.**

The blockifier checks `paid_fee_on_l1` exactly once, and only for zero:

```rust
// crates/blockifier/src/transaction/l1_handler_transaction.rs:103-113
let paid_fee = self.paid_fee_on_l1;
// For now, assert only that any amount of fee was paid.
if paid_fee == Fee(0) {
    return Err(...InsufficientFee { paid_fee, actual_fee: receipt.fee });
}
```

`Fee(1)` is non-zero, so execution always succeeds regardless of what the L1 sender actually paid.

**Step 4 — The wrong value is committed to the central blob.**

`CentralL1HandlerTransaction` serialises `paid_fee_on_l1` verbatim into the blob that is sent to the central system for proof generation:

```rust
// crates/apollo_consensus_orchestrator/src/cende/central_objects.rs:383-394
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            ...
            paid_fee_on_l1: tx.paid_fee_on_l1,   // always Fee(1) on validators
        }
    }
}
```

The proposer holds the real fee (scraped from the `LogMessageToL2` event). Every validator reconstructs `Fee(1)`. The two sides therefore produce divergent `CentralL1HandlerTransaction` objects for the same transaction.

---

### Impact Explanation

**Wrong receipt / central-blob value (Critical).** The `paid_fee_on_l1` field written into the central blob by validator nodes is always `Fee(1)` regardless of the actual ETH paid on L1. The proposer writes the real fee; validators write `Fee(1)`. This is a divergent, authoritative-looking wrong value in the data structure used for proof generation.

**Wrong RPC simulation / trace value (High).** `starknet_estimateFee`, `starknet_simulateTransactions`, and `starknet_traceTransaction` all re-execute stored L1 handler transactions via `stored_txn_to_executable_txn`, which also injects `Fee(1)`. Any client querying the fee paid on L1 for a historical L1 handler transaction receives `1` instead of the actual value.

---

### Likelihood Explanation

Every L1 handler transaction that travels through the consensus P2P path (i.e., every L1 handler transaction on a multi-validator network) triggers this substitution. The TODO comments in both locations confirm the developers are aware the value is wrong and intend to fix it, but no fix is present. The condition is unconditional and requires no special attacker input — it fires for every normal L1-to-L2 message.

---

### Recommendation

1. **Carry `paid_fee_on_l1` through the consensus wire format.** Add a `paid_fee_on_l1` field to `ConsensusTransaction::L1Handler` (or to the protobuf `L1HandlerV0` message) so the value survives serialisation and deserialisation.

2. **Remove the hardcoded `Fee(1)` placeholder** in `convert_consensus_l1_handler_to_internal_l1_handler` and in `stored_txn_to_executable_txn` once the field is available on the wire.

3. **Strengthen the blockifier fee guard** to compare `paid_fee_on_l1` against the actual computed fee, not merely against zero, so that a future regression cannot silently pass a trivially small value.

---

### Proof of Concept

The following trace reconstructs the divergence without any privileged access:

```
Proposer:
  L1 event (fee = 50_000 wei)
  → ExecutableL1HandlerTransaction { paid_fee_on_l1: Fee(50_000) }
  → InternalConsensusTransaction::L1Handler { paid_fee_on_l1: Fee(50_000) }
  → ConsensusTransaction::L1Handler(tx.tx)          // Fee(50_000) DROPPED
  → protobuf::L1HandlerV0 { nonce, address, selector, calldata }  // no fee field

Validator (receives protobuf, reconstructs):
  protobuf::L1HandlerV0 → ConsensusTransaction::L1Handler(tx)
  → convert_consensus_l1_handler_to_internal_l1_handler(tx)
      L1HandlerTransaction::create(tx, &chain_id, Fee(1))   // hardcoded
  → InternalConsensusTransaction::L1Handler { paid_fee_on_l1: Fee(1) }

Blockifier check on validator:
  paid_fee = Fee(1); Fee(1) != Fee(0) → Ok(())   // passes

CentralL1HandlerTransaction on validator:
  paid_fee_on_l1: Fee(1)   // WRONG — should be Fee(50_000)

CentralL1HandlerTransaction on proposer:
  paid_fee_on_l1: Fee(50_000)   // correct

→ Divergent central blobs for the same L1 handler transaction.
```

The divergence is deterministic and reproducible for every L1 handler transaction on any multi-validator deployment. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L178-180)
```rust
            InternalConsensusTransaction::L1Handler(tx) => {
                Ok(ConsensusTransaction::L1Handler(tx.tx))
            }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L473-483)
```rust
    fn convert_consensus_l1_handler_to_internal_l1_handler(
        &self,
        tx: transaction::L1HandlerTransaction,
    ) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
        Ok(executable_transaction::L1HandlerTransaction::create(
            tx,
            &self.chain_id,
            // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
            Fee(1),
        )?)
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L987-995)
```rust
impl From<L1HandlerTransaction> for protobuf::L1HandlerV0 {
    fn from(value: L1HandlerTransaction) -> Self {
        Self {
            nonce: Some(value.nonce.0.into()),
            address: Some(value.contract_address.into()),
            entry_point_selector: Some(value.entry_point_selector.0.into()),
            calldata: value.calldata.0.iter().map(|calldata| (*calldata).into()).collect(),
        }
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1019-1022)
```rust
            ConsensusTransaction::L1Handler(txn) => protobuf::ConsensusTransaction {
                txn: Some(protobuf::consensus_transaction::Txn::L1Handler(txn.into())),
                transaction_hash: None,
            },
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L103-113)
```rust
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```

**File:** crates/apollo_consensus_orchestrator/src/cende/central_objects.rs (L383-394)
```rust
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            hash_value: tx.tx_hash,
            contract_address: tx.tx.contract_address,
            entry_point_selector: tx.tx.entry_point_selector,
            calldata: tx.tx.calldata,
            nonce: tx.tx.nonce,
            paid_fee_on_l1: tx.paid_fee_on_l1,
        }
    }
}
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L424-428)
```rust
        starknet_api::transaction::Transaction::L1Handler(value) => {
            // todo(yair): This is a temporary solution until we have a better way to get the l1
            // fee.
            let paid_fee_on_l1 = Fee(1);
            Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
```

**File:** crates/starknet_api/src/consensus_transaction.rs (L8-18)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum InternalConsensusTransaction {
    RpcTransaction(InternalRpcTransaction),
    L1Handler(executable_transaction::L1HandlerTransaction),
}
```

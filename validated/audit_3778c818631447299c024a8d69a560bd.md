### Title
`paid_fee_on_l1` Silently Hardcoded to `Fee(1)` During Consensus-to-Internal L1Handler Conversion, Producing Wrong Executable Payload and Incorrect Cende Blob Fee Data - (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

When a validator node converts a received `ConsensusTransaction::L1Handler` into an `InternalConsensusTransaction::L1Handler` for blockifier execution, the `paid_fee_on_l1` field is unconditionally hardcoded to `Fee(1)` instead of carrying the actual fee paid on L1. This is a direct analog of the external bug: the transaction body fields (nonce, contract address, calldata, entry point selector) are faithfully preserved across the conversion boundary, but the economically significant `paid_fee_on_l1` sub-field is silently substituted with a dummy value. The result is that the executable object bound to blockifier on the validator path always carries the wrong fee, the cende blob records wrong fee data for every L1Handler transaction, and a proposer/validator execution divergence is possible whenever the real `paid_fee_on_l1` is `Fee(0)`.

### Finding Description

`ConsensusTransaction` carries L1Handler transactions as `transaction::L1HandlerTransaction`, which has no `paid_fee_on_l1` field:

```rust
// crates/starknet_api/src/consensus_transaction.rs
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),   // no paid_fee_on_l1
}
``` [1](#0-0) 

`InternalConsensusTransaction`, by contrast, carries the executable form which does have `paid_fee_on_l1`:

```rust
pub enum InternalConsensusTransaction {
    RpcTransaction(InternalRpcTransaction),
    L1Handler(executable_transaction::L1HandlerTransaction),  // has paid_fee_on_l1
}
``` [2](#0-1) 

The conversion from `ConsensusTransaction::L1Handler` to `InternalConsensusTransaction::L1Handler` is performed in `convert_consensus_l1_handler_to_internal_l1_handler`:

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
``` [3](#0-2) 

The `paid_fee_on_l1` is structurally absent from `ConsensusTransaction::L1Handler` because the proposer drops it when converting `InternalConsensusTransaction::L1Handler` → `ConsensusTransaction::L1Handler`:

```rust
InternalConsensusTransaction::L1Handler(tx) => {
    Ok(ConsensusTransaction::L1Handler(tx.tx))  // tx.paid_fee_on_l1 dropped
}
``` [4](#0-3) 

The `executable_transaction::L1HandlerTransaction` struct that is ultimately handed to blockifier therefore always has `paid_fee_on_l1 = Fee(1)` on the validator path:

```rust
pub struct L1HandlerTransaction {
    pub tx: crate::transaction::L1HandlerTransaction,
    pub tx_hash: TransactionHash,
    pub paid_fee_on_l1: Fee,   // always Fee(1) on validator path
}
``` [5](#0-4) 

The blockifier's fee check for L1Handler transactions reads:

```rust
let paid_fee = self.paid_fee_on_l1;
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(Box::new(
        TransactionFeeError::InsufficientFee { paid_fee, actual_fee: receipt.fee },
    )));
}
``` [6](#0-5) 

Since `Fee(1) != Fee(0)`, the check always passes on the validator path regardless of the actual fee paid on L1.

The cende blob serialization also reads `paid_fee_on_l1` directly from the executable transaction:

```rust
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            ...
            paid_fee_on_l1: tx.paid_fee_on_l1,  // always Fee(1) on validator path
        }
    }
}
``` [7](#0-6) 

### Impact Explanation

**Wrong executable payload bound to blockifier on validator path.** Every `InternalConsensusTransaction::L1Handler` produced by a validator carries `paid_fee_on_l1 = Fee(1)` regardless of the actual L1 fee. This is the wrong executable object.

**Incorrect cende blob fee data.** The central blob sent to the prover/cende system records `paid_fee_on_l1 = Fee(1)` for all L1Handler transactions processed on the validator path, corrupting the economic record.

**Proposer/validator execution divergence.** If the proposer's L1 event processing yields `paid_fee_on_l1 = Fee(0)` (e.g., due to a bug in L1 event scraping or a zero-fee L1 message), the proposer's blockifier returns `InsufficientFee` and marks the transaction as reverted, while the validator's blockifier (using `Fee(1)`) marks it as successful. This produces different state diffs and receipts, breaking consensus.

### Likelihood Explanation

The divergence path requires `paid_fee_on_l1 = Fee(0)` from the proposer's L1 event source, which is unlikely for well-formed L1 events. However, the wrong cende blob data is unconditional and affects every L1Handler transaction processed through the consensus validation path. The issue is confirmed by the `TODO(Gilad)` comment acknowledging the placeholder value.

### Recommendation

`paid_fee_on_l1` must be carried through the consensus message boundary. The fix requires adding `paid_fee_on_l1` to `ConsensusTransaction::L1Handler` (i.e., switching from `transaction::L1HandlerTransaction` to a wrapper that includes the fee), updating the protobuf schema for `L1HandlerV0` to include the field, and propagating it through `convert_consensus_l1_handler_to_internal_l1_handler` instead of hardcoding `Fee(1)`.

### Proof of Concept

1. Proposer receives an L1 event with `paid_fee_on_l1 = Fee(0)` (edge case or bug in L1 scraper).
2. Proposer's blockifier executes the L1Handler, hits `if paid_fee == Fee(0)` at line 106 of `l1_handler_transaction.rs`, returns `InsufficientFee`, transaction is reverted.
3. Proposer broadcasts the block with the reverted L1Handler transaction.
4. Validator receives `ConsensusTransaction::L1Handler(tx)` — `paid_fee_on_l1` is absent from the wire type.
5. Validator calls `convert_consensus_l1_handler_to_internal_l1_handler`, which calls `L1HandlerTransaction::create(tx, &self.chain_id, Fee(1))`.
6. Validator's blockifier executes the same L1Handler with `paid_fee_on_l1 = Fee(1)`, the check `Fee(1) == Fee(0)` is false, execution succeeds.
7. Validator produces a different state diff and receipt than the proposer — consensus is broken.

For the unconditional cende blob corruption: every L1Handler transaction processed by any non-proposing validator node will have `paid_fee_on_l1 = Fee(1)` in the blob regardless of the actual L1 fee, as shown by the `From<L1HandlerTransaction> for CentralL1HandlerTransaction` conversion at lines 383–393 of `central_objects.rs`.

### Citations

**File:** crates/starknet_api/src/consensus_transaction.rs (L8-12)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),
}
```

**File:** crates/starknet_api/src/consensus_transaction.rs (L14-18)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum InternalConsensusTransaction {
    RpcTransaction(InternalRpcTransaction),
    L1Handler(executable_transaction::L1HandlerTransaction),
}
```

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

**File:** crates/starknet_api/src/executable_transaction.rs (L380-385)
```rust
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub struct L1HandlerTransaction {
    pub tx: crate::transaction::L1HandlerTransaction,
    pub tx_hash: TransactionHash,
    pub paid_fee_on_l1: Fee,
}
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

**File:** crates/apollo_consensus_orchestrator/src/cende/central_objects.rs (L383-393)
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
```

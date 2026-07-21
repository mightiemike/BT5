### Title
Hardcoded `paid_fee_on_l1 = Fee(1)` in Consensus L1 Handler Conversion with Too-Relaxed Fee Check — (`crates/apollo_transaction_converter/src/transaction_converter.rs`, `crates/blockifier/src/transaction/l1_handler_transaction.rs`)

### Summary

The consensus-path conversion of `L1HandlerTransaction` hardcodes `paid_fee_on_l1 = Fee(1)` for every L1 handler transaction received through consensus. The blockifier's post-execution fee check only verifies `paid_fee != Fee(0)` — i.e., any nonzero value passes — instead of verifying `paid_fee >= actual_fee`. The combination means every L1 handler transaction arriving via consensus unconditionally passes the fee check, and the wrong `paid_fee_on_l1` value is committed to block data and forwarded to the central server.

### Finding Description

**Conversion boundary — hardcoded placeholder fee:**

In `convert_consensus_l1_handler_to_internal_l1_handler`, the `paid_fee_on_l1` field is unconditionally set to `Fee(1)`:

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
``` [1](#0-0) 

This is called for every `ConsensusTransaction::L1Handler` in `convert_consensus_tx_to_internal_consensus_tx`: [2](#0-1) 

**Fee check — too-relaxed guard:**

The blockifier's L1 handler execution path checks only that `paid_fee != Fee(0)`, explicitly acknowledging it does not verify sufficiency:

```rust
// For now, assert only that any amount of fee was paid.
// The error message still indicates the required fee.
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(...));
}
``` [3](#0-2) 

The check is `paid_fee > 0` rather than `paid_fee >= actual_fee`. With the hardcoded `Fee(1)`, this guard is always satisfied regardless of the actual fee required.

**Wrong value committed to block data:**

`paid_fee_on_l1` is serialized into `CentralL1HandlerTransaction` and forwarded to the central server as part of block commitment: [4](#0-3) 

The `ConsensusTransaction::L1Handler` wire type carries only the raw `transaction::L1HandlerTransaction` (no `paid_fee_on_l1` field), so the conversion must supply the value — but it always supplies `Fee(1)` instead of the actual L1-paid fee. [5](#0-4) 

### Impact Explanation

Every L1 handler transaction processed through the consensus path is committed with `paid_fee_on_l1 = Fee(1)`, regardless of the actual fee paid on L1. This produces a wrong value in the committed block receipt/state for every such transaction. The too-relaxed check (`paid_fee != 0` instead of `paid_fee >= actual_fee`) means no execution-time gate catches the discrepancy.

**Impact category:** Critical — Wrong state, receipt, or event from blockifier/execution logic for accepted input; Incorrect fee/resource accounting with economic impact.

### Likelihood Explanation

Every L1 handler transaction that arrives via the consensus path (i.e., from a peer proposer rather than directly from L1 scraping) triggers this code path. The `TODO` comment confirms this is a known placeholder, not an intentional design. Any block containing L1 handler transactions proposed by a peer will commit wrong `paid_fee_on_l1` values.

### Recommendation

1. **Preserve the actual `paid_fee_on_l1` through the consensus wire format.** Add `paid_fee_on_l1` to the `ConsensusTransaction::L1Handler` protobuf message and propagate it through `convert_consensus_l1_handler_to_internal_l1_handler` instead of hardcoding `Fee(1)`.

2. **Strengthen the fee check.** Replace the `paid_fee == Fee(0)` guard with `paid_fee < actual_fee` (i.e., `paid_fee >= actual_fee` required), analogous to requiring `received == _amount` rather than `received > 0`. The current comment "For now, assert only that any amount of fee was paid" should be resolved.

### Proof of Concept

1. A peer proposer includes an L1 handler transaction in a consensus proposal. The transaction was originally triggered by an L1 message with `paid_fee_on_l1 = Fee(1_000_000)`.
2. The proposal is received and `convert_consensus_tx_to_internal_consensus_tx` is called.
3. `convert_consensus_l1_handler_to_internal_l1_handler` sets `paid_fee_on_l1 = Fee(1)`.
4. The blockifier executes the transaction; `paid_fee == Fee(1) != Fee(0)`, so the check passes.
5. `CentralL1HandlerTransaction` is serialized with `paid_fee_on_l1 = Fee(1)` and committed to the block.
6. The committed block receipt contains the wrong `paid_fee_on_l1` value for this transaction, diverging from the actual L1 payment.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L197-200)
```rust
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
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

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L101-113)
```rust
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1019-1023)
```rust
            ConsensusTransaction::L1Handler(txn) => protobuf::ConsensusTransaction {
                txn: Some(protobuf::consensus_transaction::Txn::L1Handler(txn.into())),
                transaction_hash: None,
            },
        }
```

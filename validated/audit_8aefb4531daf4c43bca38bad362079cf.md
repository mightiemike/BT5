### Title
`paid_fee_on_l1` silently dropped in consensus P2P serialization and hardcoded to `Fee(1)` on all validator nodes, causing wrong fee data in Cende blob - (File: `crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary
The `L1HandlerV0` protobuf message used in consensus P2P does not carry the `paid_fee_on_l1` field. When a validator node receives an `L1Handler` transaction via consensus P2P and converts it to `InternalConsensusTransaction::L1Handler`, the `paid_fee_on_l1` is unconditionally hardcoded to `Fee(1)` instead of the actual fee paid on L1. When that validator later becomes the proposer and writes the Cende blob for the previous block, the blob contains `paid_fee_on_l1 = Fee(1)` for every L1Handler transaction, regardless of the actual fee paid on L1.

### Finding Description

The conversion path for L1Handler transactions through consensus is:

**Step 1 – Proposer acquires real fee from L1 events provider:**
`ProposeTransactionProvider::get_l1_handler_txs` wraps `L1HandlerTransaction` objects directly as `InternalConsensusTransaction::L1Handler`, preserving the real `paid_fee_on_l1` from the L1 events scraper. [1](#0-0) 

**Step 2 – Broadcast drops `paid_fee_on_l1`:**
`convert_internal_consensus_tx_to_consensus_tx` converts `InternalConsensusTransaction::L1Handler(tx)` to `ConsensusTransaction::L1Handler(tx.tx)`, silently discarding `tx.paid_fee_on_l1`. [2](#0-1) 

**Step 3 – Protobuf schema has no `paid_fee_on_l1` field:**
`L1HandlerV0` only carries `nonce`, `address`, `entry_point_selector`, and `calldata`. There is no field for `paid_fee_on_l1`. [3](#0-2) 

**Step 4 – Validator hardcodes `Fee(1)` with an acknowledged TODO:**
`convert_consensus_l1_handler_to_internal_l1_handler` reconstructs the executable transaction with a hardcoded `Fee(1)`. [4](#0-3) 

**Step 5 – Cende blob propagates the wrong value:**
`CentralL1HandlerTransaction::from` copies `paid_fee_on_l1` directly from the stored `L1HandlerTransaction`. Any node that was a validator for block N will write `paid_fee_on_l1 = Fee(1)` into the Cende blob when it becomes the proposer for block N+1. [5](#0-4) 

The exact divergent value: the proposer for block N stores the real `paid_fee_on_l1` (e.g., `Fee(1_000_000_000)`); every other node stores `Fee(1)`. The Cende blob written by any non-original-proposer node carries `Fee(1)` for every L1Handler transaction.

Additionally, the blockifier's only fee guard is `if paid_fee == Fee(0) { return Err(InsufficientFee) }`. With `Fee(1)` hardcoded, this guard is permanently bypassed on all validator nodes, meaning the fee-sufficiency invariant is never enforced during block validation. [6](#0-5) 

### Impact Explanation
The Cende blob is the authoritative pre-confirmation record sent to the central Starknet infrastructure. `paid_fee_on_l1` in `CentralL1HandlerTransaction` represents the economic value transferred from L1 to L2 for each L1Handler message. Nodes that validated (rather than proposed) a block will always write `Fee(1)` into this field, producing a systematically wrong fee record for every L1Handler transaction in every block they did not originally propose. This constitutes incorrect fee/resource accounting with direct economic impact on the central system's view of L1→L2 message fees.

### Likelihood Explanation
In a rotating-proposer BFT network, every node is a validator for the majority of blocks. Every L1Handler transaction included in any block triggers this issue on all non-proposing validators. The Cende blob is written by the next proposer, which is almost always a different node from the one that proposed the block containing the L1Handler transaction. The issue fires on every L1Handler transaction in production.

### Recommendation
1. Add a `paid_fee_on_l1` field (e.g., `uint64` or `Felt252`) to the `L1HandlerV0` protobuf message in `consensus.proto`.
2. Update `From<L1HandlerTransaction> for protobuf::L1HandlerV0` and `TryFrom<protobuf::L1HandlerV0> for L1HandlerTransaction` to serialize/deserialize `paid_fee_on_l1`.
3. Remove the hardcoded `Fee(1)` in `convert_consensus_l1_handler_to_internal_l1_handler` and use the value carried in the deserialized protobuf message.

### Proof of Concept

```
1. Node A (proposer for block N):
   - L1 events provider returns L1Handler tx with paid_fee_on_l1 = Fee(5_000_000_000)
   - Stored internally as InternalConsensusTransaction::L1Handler { paid_fee_on_l1: Fee(5_000_000_000) }
   - Broadcast via P2P as protobuf::L1HandlerV0 { nonce, address, selector, calldata }
     (paid_fee_on_l1 is absent from the wire message)

2. Node B (validator for block N):
   - Receives protobuf::L1HandlerV0 (no paid_fee_on_l1 field)

### Citations

**File:** crates/apollo_batcher/src/transaction_provider.rs (L94-110)
```rust
    async fn get_l1_handler_txs(
        &mut self,
        n_txs: usize,
    ) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
        Ok(self
            .l1_events_provider_client
            .get_txs(n_txs, self.height)
            .await
            .inspect_err(|err| {
                warn!("L1 provider error while fetching L1 handler transactions: {:?}", err);
                BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
            })
            .unwrap_or_default()
            .into_iter()
            .map(InternalConsensusTransaction::L1Handler)
            .collect())
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L178-181)
```rust
            InternalConsensusTransaction::L1Handler(tx) => {
                Ok(ConsensusTransaction::L1Handler(tx.tx))
            }
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

**File:** crates/apollo_protobuf/src/protobuf/protoc_output.rs (L240-249)
```rust
pub struct L1HandlerV0 {
    #[prost(message, optional, tag = "1")]
    pub nonce: ::core::option::Option<Felt252>,
    #[prost(message, optional, tag = "2")]
    pub address: ::core::option::Option<Address>,
    #[prost(message, optional, tag = "3")]
    pub entry_point_selector: ::core::option::Option<Felt252>,
    #[prost(message, repeated, tag = "4")]
    pub calldata: ::prost::alloc::vec::Vec<Felt252>,
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

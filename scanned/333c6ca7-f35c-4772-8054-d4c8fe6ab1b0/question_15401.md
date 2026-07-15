# Q15401: shuffle receipt proofs receipt ordering invariant

## Question

What can an unprivileged user do by submitting signed transactions that become chunk transactions and receipts in block public inputs so that `shuffle_receipt_proofs` in `chain/chain/src/sharding.rs` processes a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas along the block, chunk, and runtime adapter processing path? User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> `shuffle_receipt_proofs` processes that value during receipt creation, local execution, incoming receipt application, and delayed queue draining -> the receipt ordering preserves dependency edges, shard routing, and exactly-once execution invariant might break -> potential in-scope impact is transaction manipulation, balance manipulation, or contract execution flow corruption under the NEAR HackenProof scope. Exploit hypothesis: a user-shaped promise DAG can make this code accept or execute receipts in an order that breaks dependency preservation and changes balances or callback results, violating the actual protocol invariant that receipt ordering preserves dependency edges, shard routing, and exactly-once execution.

## Target

- File/function: chain/chain/src/sharding.rs:13::shuffle_receipt_proofs
- Entrypoint: user transaction included in a block processed by chain/chain/src/chain.rs::Chain::start_process_block_async
- User-controlled input: a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas
- Attack path: User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> public entrypoint reaches `shuffle_receipt_proofs` -> receipt creation, local execution, incoming receipt application, and delayed queue draining handles the value -> invariant failure could produce transaction manipulation, balance manipulation, or contract execution flow corruption
- Security invariant: receipt ordering preserves dependency edges, shard routing, and exactly-once execution
- Expected bounty impact: transaction manipulation, balance manipulation, or contract execution flow corruption
- Fast validation approach: build a test-loop scenario with cross-shard promises, callbacks, refunds, and delayed receipts, then compare outcomes, receipt IDs, and final state roots across nodes

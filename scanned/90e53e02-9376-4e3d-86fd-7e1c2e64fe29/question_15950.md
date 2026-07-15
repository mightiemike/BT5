# Q15950: set promise yield indices receipt ordering invariant

## Question

What can an unprivileged user do by writing contract storage, creating/deleting accounts, and generating state/proof boundary cases through valid transactions so that `set_promise_yield_indices` in `core/store/src/utils/mod.rs` processes a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas along the trie, flat storage, state sync, and proofs path? User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> `set_promise_yield_indices` processes that value during receipt creation, local execution, incoming receipt application, and delayed queue draining -> the receipt ordering preserves dependency edges, shard routing, and exactly-once execution invariant might break -> potential in-scope impact is transaction manipulation, balance manipulation, or contract execution flow corruption under the NEAR HackenProof scope. Exploit hypothesis: a user-shaped promise DAG can make this code accept or execute receipts in an order that breaks dependency preservation and changes balances or callback results, violating the actual protocol invariant that receipt ordering preserves dependency edges, shard routing, and exactly-once execution.

## Target

- File/function: core/store/src/utils/mod.rs:156::set_promise_yield_indices
- Entrypoint: contract storage and account actions committed through Runtime::apply into core/store trie and flat-state paths
- User-controlled input: a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas
- Attack path: User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> public entrypoint reaches `set_promise_yield_indices` -> receipt creation, local execution, incoming receipt application, and delayed queue draining handles the value -> invariant failure could produce transaction manipulation, balance manipulation, or contract execution flow corruption
- Security invariant: receipt ordering preserves dependency edges, shard routing, and exactly-once execution
- Expected bounty impact: transaction manipulation, balance manipulation, or contract execution flow corruption
- Fast validation approach: build a test-loop scenario with cross-shard promises, callbacks, refunds, and delayed receipts, then compare outcomes, receipt IDs, and final state roots across nodes

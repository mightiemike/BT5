# Q12687: promise yield resume with yield id receipt ordering invariant

## Question

What can an unprivileged user do by deploying WASM bytecode and invoking exported contract methods with chosen arguments so that `promise_yield_resume_with_yield_id` in `runtime/near-vm-runner/src/wasmtime_runner/logic.rs` processes a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas along the WASM preparation and execution path? User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> `promise_yield_resume_with_yield_id` processes that value during receipt creation, local execution, incoming receipt application, and delayed queue draining -> the receipt ordering preserves dependency edges, shard routing, and exactly-once execution invariant might break -> potential in-scope impact is transaction manipulation, balance manipulation, or contract execution flow corruption under the NEAR HackenProof scope. Exploit hypothesis: a user-shaped promise DAG can make this code accept or execute receipts in an order that breaks dependency preservation and changes balances or callback results, violating the actual protocol invariant that receipt ordering preserves dependency edges, shard routing, and exactly-once execution.

## Target

- File/function: runtime/near-vm-runner/src/wasmtime_runner/logic.rs:4129::promise_yield_resume_with_yield_id
- Entrypoint: contract deployment and function call executed through runtime/near-vm-runner/src/runner.rs::run
- User-controlled input: a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas
- Attack path: User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> public entrypoint reaches `promise_yield_resume_with_yield_id` -> receipt creation, local execution, incoming receipt application, and delayed queue draining handles the value -> invariant failure could produce transaction manipulation, balance manipulation, or contract execution flow corruption
- Security invariant: receipt ordering preserves dependency edges, shard routing, and exactly-once execution
- Expected bounty impact: transaction manipulation, balance manipulation, or contract execution flow corruption
- Fast validation approach: build a test-loop scenario with cross-shard promises, callbacks, refunds, and delayed receipts, then compare outcomes, receipt IDs, and final state roots across nodes

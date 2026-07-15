# Q15973: get incoming receipts for chunk shard routing edge case

## Question

What can an unprivileged user do by submitting transactions, deploying contracts, calling methods, and creating promise receipts so that `get_incoming_receipts_for_chunk` in `runtime/runtime/src/bandwidth_scheduler/simulator.rs` processes receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues along the runtime state transition path? User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> `get_incoming_receipts_for_chunk` processes that value during account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application -> the each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch invariant might break -> potential in-scope impact is receipt loss/duplication, balance manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a boundary account ID or epoch transition can make this code route or account for a receipt on the wrong shard, violating the actual protocol invariant that each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch.

## Target

- File/function: runtime/runtime/src/bandwidth_scheduler/simulator.rs:373::get_incoming_receipts_for_chunk
- Entrypoint: signed transaction submitted through public RPC and applied by runtime/runtime/src/lib.rs::Runtime::apply
- User-controlled input: receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues
- Attack path: User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> public entrypoint reaches `get_incoming_receipts_for_chunk` -> account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application handles the value -> invariant failure could produce receipt loss/duplication, balance manipulation, or state desynchronization
- Security invariant: each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch
- Expected bounty impact: receipt loss/duplication, balance manipulation, or state desynchronization
- Fast validation approach: craft accounts around shard-boundary names and resharding epochs, then assert receipt roots, queue lengths, outcomes, and balances remain consistent

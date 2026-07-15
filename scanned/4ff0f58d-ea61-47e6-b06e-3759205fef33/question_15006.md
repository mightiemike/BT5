# Q15006: chunk sharded shard routing edge case

## Question

What can an unprivileged user do by calling a public RPC method or submitting a signed transaction through broadcast_tx_* or query endpoints so that `chunk_sharded` in `chain/jsonrpc/src/lib.rs` processes receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues along the RPC validation and forwarding path? User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> `chunk_sharded` processes that value during account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application -> the each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch invariant might break -> potential in-scope impact is receipt loss/duplication, balance manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a boundary account ID or epoch transition can make this code route or account for a receipt on the wrong shard, violating the actual protocol invariant that each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch.

## Target

- File/function: chain/jsonrpc/src/lib.rs:1856::chunk_sharded
- Entrypoint: public JSON-RPC request handled by chain/jsonrpc/src/lib.rs::JsonRpcHandler::process
- User-controlled input: receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues
- Attack path: User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> public entrypoint reaches `chunk_sharded` -> account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application handles the value -> invariant failure could produce receipt loss/duplication, balance manipulation, or state desynchronization
- Security invariant: each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch
- Expected bounty impact: receipt loss/duplication, balance manipulation, or state desynchronization
- Fast validation approach: craft accounts around shard-boundary names and resharding epochs, then assert receipt roots, queue lengths, outcomes, and balances remain consistent

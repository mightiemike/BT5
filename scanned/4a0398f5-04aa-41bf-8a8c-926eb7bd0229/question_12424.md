# Q12424: get value shard routing edge case

## Question

What can an unprivileged user do by writing contract storage, creating/deleting accounts, and generating state/proof boundary cases through valid transactions so that `get_value` in `core/store/src/flat/chunk_view.rs` (impl FlatStorageChunkView) processes receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues along the trie, flat storage, state sync, and proofs path? User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> `get_value` processes that value during account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application -> the each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch invariant might break -> potential in-scope impact is receipt loss/duplication, balance manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a boundary account ID or epoch transition can make this code route or account for a receipt on the wrong shard, violating the actual protocol invariant that each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch.

## Target

- File/function: core/store/src/flat/chunk_view.rs:41::get_value
- Entrypoint: contract storage and account actions committed through Runtime::apply into core/store trie and flat-state paths
- User-controlled input: receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues
- Attack path: User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> public entrypoint reaches `get_value` -> account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application handles the value -> invariant failure could produce receipt loss/duplication, balance manipulation, or state desynchronization
- Security invariant: each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch
- Expected bounty impact: receipt loss/duplication, balance manipulation, or state desynchronization
- Fast validation approach: craft accounts around shard-boundary names and resharding epochs, then assert receipt roots, queue lengths, outcomes, and balances remain consistent

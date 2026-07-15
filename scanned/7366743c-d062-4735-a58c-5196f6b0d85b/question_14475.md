# Q14475: get merkle hash and paths shard routing edge case

## Question

What can an unprivileged user do by submitting encoded transactions, receipts created by contracts, account IDs, proofs, and JSON/RPC parameters so that `get_merkle_hash_and_paths` in `core/primitives/src/sharding.rs` (impl EncodedShardChunkBody) processes receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues along the protocol primitive validation, hashing, and serialization path? User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> `get_merkle_hash_and_paths` processes that value during account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application -> the each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch invariant might break -> potential in-scope impact is receipt loss/duplication, balance manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a boundary account ID or epoch transition can make this code route or account for a receipt on the wrong shard, violating the actual protocol invariant that each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch.

## Target

- File/function: core/primitives/src/sharding.rs:1218::get_merkle_hash_and_paths
- Entrypoint: public RPC transaction/query input decoded into core/primitives protocol objects
- User-controlled input: receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues
- Attack path: User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> public entrypoint reaches `get_merkle_hash_and_paths` -> account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application handles the value -> invariant failure could produce receipt loss/duplication, balance manipulation, or state desynchronization
- Security invariant: each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch
- Expected bounty impact: receipt loss/duplication, balance manipulation, or state desynchronization
- Fast validation approach: craft accounts around shard-boundary names and resharding epochs, then assert receipt roots, queue lengths, outcomes, and balances remain consistent

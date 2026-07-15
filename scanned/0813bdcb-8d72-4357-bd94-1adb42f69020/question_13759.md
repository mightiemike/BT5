# Q13759: requeue waiting witnesses shard routing edge case

## Question

What can an unprivileged user do by submitting encoded transactions, receipts created by contracts, account IDs, proofs, and JSON/RPC parameters so that `requeue_waiting_witnesses` in `chain/client/src/spice/chunk_validator_actor.rs` processes receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues along the protocol primitive validation, hashing, and serialization path? User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> `requeue_waiting_witnesses` processes that value during account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application -> the each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch invariant might break -> potential in-scope impact is receipt loss/duplication, balance manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a boundary account ID or epoch transition can make this code route or account for a receipt on the wrong shard, violating the actual protocol invariant that each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch.

## Target

- File/function: chain/client/src/spice/chunk_validator_actor.rs:406::requeue_waiting_witnesses
- Entrypoint: public RPC transaction/query input decoded into core/primitives protocol objects
- User-controlled input: receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues
- Attack path: User controls receiver account IDs, predecessor IDs, implicit accounts, promise callbacks, and cross-shard receipt queues -> public entrypoint reaches `requeue_waiting_witnesses` -> account-to-shard mapping, outgoing receipt routing, bandwidth scheduling, and chunk application handles the value -> invariant failure could produce receipt loss/duplication, balance manipulation, or state desynchronization
- Security invariant: each transaction or receipt is charged on the source shard and executed exactly once on the receiver shard selected for that epoch
- Expected bounty impact: receipt loss/duplication, balance manipulation, or state desynchronization
- Fast validation approach: craft accounts around shard-boundary names and resharding epochs, then assert receipt roots, queue lengths, outcomes, and balances remain consistent

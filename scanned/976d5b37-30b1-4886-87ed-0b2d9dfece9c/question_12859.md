# Q12859: pool for shard proof verification boundary

## Question

What can an unprivileged user do by submitting transactions and contract calls that produce chunk transactions and outgoing receipts so that `pool_for_shard` in `chain/chunks/src/client.rs` (impl ShardedTransactionPool) processes Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data along the chunk production, distribution, and validation path? User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> `pool_for_shard` processes that value during proof decoding, path verification, root comparison, and chunk/state validation -> the proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions invariant might break -> potential in-scope impact is state sync inconsistency, consensus flaw, or proof verification bypass under the NEAR HackenProof scope. Exploit hypothesis: a malformed but protocol-shaped proof can make this code accept data not committed by the referenced root, violating the actual protocol invariant that proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions.

## Target

- File/function: chain/chunks/src/client.rs:91::pool_for_shard
- Entrypoint: user transaction converted into chunk contents consumed by chain/chunks shard processing
- User-controlled input: Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data
- Attack path: User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> public entrypoint reaches `pool_for_shard` -> proof decoding, path verification, root comparison, and chunk/state validation handles the value -> invariant failure could produce state sync inconsistency, consensus flaw, or proof verification bypass
- Security invariant: proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions
- Expected bounty impact: state sync inconsistency, consensus flaw, or proof verification bypass
- Fast validation approach: mutate proof indexes, sibling order, empty paths, duplicated hashes, and stale roots while asserting all invalid public proofs are rejected

# Q13838: block request due proof verification boundary

## Question

What can an unprivileged user do by submitting encoded transactions, receipts created by contracts, account IDs, proofs, and JSON/RPC parameters so that `block_request_due` in `chain/client/src/sync/block.rs` processes Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data along the protocol primitive validation, hashing, and serialization path? User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> `block_request_due` processes that value during proof decoding, path verification, root comparison, and chunk/state validation -> the proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions invariant might break -> potential in-scope impact is state sync inconsistency, consensus flaw, or proof verification bypass under the NEAR HackenProof scope. Exploit hypothesis: a malformed but protocol-shaped proof can make this code accept data not committed by the referenced root, violating the actual protocol invariant that proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions.

## Target

- File/function: chain/client/src/sync/block.rs:214::block_request_due
- Entrypoint: public RPC transaction/query input decoded into core/primitives protocol objects
- User-controlled input: Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data
- Attack path: User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> public entrypoint reaches `block_request_due` -> proof decoding, path verification, root comparison, and chunk/state validation handles the value -> invariant failure could produce state sync inconsistency, consensus flaw, or proof verification bypass
- Security invariant: proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions
- Expected bounty impact: state sync inconsistency, consensus flaw, or proof verification bypass
- Fast validation approach: mutate proof indexes, sibling order, empty paths, duplicated hashes, and stale roots while asserting all invalid public proofs are rejected

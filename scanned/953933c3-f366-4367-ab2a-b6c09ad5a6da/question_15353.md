# Q15353: should be pending execution proof verification boundary

## Question

What can an unprivileged user do by submitting signed transactions that become chunk transactions and receipts in block public inputs so that `should_be_pending_execution` in `chain/chain/src/chain.rs` processes Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data along the block, chunk, and runtime adapter processing path? User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> `should_be_pending_execution` processes that value during proof decoding, path verification, root comparison, and chunk/state validation -> the proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions invariant might break -> potential in-scope impact is state sync inconsistency, consensus flaw, or proof verification bypass under the NEAR HackenProof scope. Exploit hypothesis: a malformed but protocol-shaped proof can make this code accept data not committed by the referenced root, violating the actual protocol invariant that proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions.

## Target

- File/function: chain/chain/src/chain.rs:3201::should_be_pending_execution
- Entrypoint: user transaction included in a block processed by chain/chain/src/chain.rs::Chain::start_process_block_async
- User-controlled input: Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data
- Attack path: User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> public entrypoint reaches `should_be_pending_execution` -> proof decoding, path verification, root comparison, and chunk/state validation handles the value -> invariant failure could produce state sync inconsistency, consensus flaw, or proof verification bypass
- Security invariant: proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions
- Expected bounty impact: state sync inconsistency, consensus flaw, or proof verification bypass
- Fast validation approach: mutate proof indexes, sibling order, empty paths, duplicated hashes, and stale roots while asserting all invalid public proofs are rejected

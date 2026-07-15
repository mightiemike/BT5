# Q15383: init flat storage for next epoch proof verification boundary

## Question

What can an unprivileged user do by submitting signed transactions that become chunk transactions and receipts in block public inputs so that `init_flat_storage_for_next_epoch` in `chain/chain/src/flat_storage_init.rs` (impl Chain) processes Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data along the block, chunk, and runtime adapter processing path? User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> `init_flat_storage_for_next_epoch` processes that value during proof decoding, path verification, root comparison, and chunk/state validation -> the proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions invariant might break -> potential in-scope impact is state sync inconsistency, consensus flaw, or proof verification bypass under the NEAR HackenProof scope. Exploit hypothesis: a malformed but protocol-shaped proof can make this code accept data not committed by the referenced root, violating the actual protocol invariant that proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions.

## Target

- File/function: chain/chain/src/flat_storage_init.rs:62::init_flat_storage_for_next_epoch
- Entrypoint: user transaction included in a block processed by chain/chain/src/chain.rs::Chain::start_process_block_async
- User-controlled input: Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data
- Attack path: User controls Merkle paths, state parts, receipt proofs, execution outcomes, and public proof indexes exposed through RPC or sync data -> public entrypoint reaches `init_flat_storage_for_next_epoch` -> proof decoding, path verification, root comparison, and chunk/state validation handles the value -> invariant failure could produce state sync inconsistency, consensus flaw, or proof verification bypass
- Security invariant: proofs authenticate exactly the claimed item, index, shard, block, and state root before affecting trust decisions
- Expected bounty impact: state sync inconsistency, consensus flaw, or proof verification bypass
- Fast validation approach: mutate proof indexes, sibling order, empty paths, duplicated hashes, and stale roots while asserting all invalid public proofs are rejected

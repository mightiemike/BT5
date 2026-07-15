# Q15098: pre validate chunk state witness epoch transition invariant

## Question

What can an unprivileged user do by submitting signed transactions that become chunk transactions and receipts in block public inputs so that `pre_validate_chunk_state_witness` in `chain/chain/src/stateless_validation/chunk_validation.rs` processes transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth along the block, chunk, and runtime adapter processing path? User controls transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth -> `pre_validate_chunk_state_witness` processes that value during epoch finalization, validator/assignment lookup, protocol feature gating, and block/chunk validation -> the epoch-specific protocol parameters, shard layouts, validators, and feature gates are applied consistently for every block height invariant might break -> potential in-scope impact is consensus flaw, transaction manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: an off-by-one epoch or feature gate edge can make this code validate user-controlled state transitions under the wrong protocol parameters, violating the actual protocol invariant that epoch-specific protocol parameters, shard layouts, validators, and feature gates are applied consistently for every block height.

## Target

- File/function: chain/chain/src/stateless_validation/chunk_validation.rs:310::pre_validate_chunk_state_witness
- Entrypoint: user transaction included in a block processed by chain/chain/src/chain.rs::Chain::start_process_block_async
- User-controlled input: transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth
- Attack path: User controls transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth -> public entrypoint reaches `pre_validate_chunk_state_witness` -> epoch finalization, validator/assignment lookup, protocol feature gating, and block/chunk validation handles the value -> invariant failure could produce consensus flaw, transaction manipulation, or state desynchronization
- Security invariant: epoch-specific protocol parameters, shard layouts, validators, and feature gates are applied consistently for every block height
- Expected bounty impact: consensus flaw, transaction manipulation, or state desynchronization
- Fast validation approach: run boundary-height tests that submit user transactions before/at/after epoch changes and compare validation paths, shard layout, gas costs, and state roots

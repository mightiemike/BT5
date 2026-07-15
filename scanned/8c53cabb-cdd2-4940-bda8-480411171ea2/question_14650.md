# Q14650: produced epoch transition invariant

## Question

What can an unprivileged user do by submitting encoded transactions, receipts created by contracts, account IDs, proofs, and JSON/RPC parameters so that `produced` in `core/primitives/src/types/chunk_validator_stats.rs` (impl ChunkStats) processes transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth along the protocol primitive validation, hashing, and serialization path? User controls transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth -> `produced` processes that value during epoch finalization, validator/assignment lookup, protocol feature gating, and block/chunk validation -> the epoch-specific protocol parameters, shard layouts, validators, and feature gates are applied consistently for every block height invariant might break -> potential in-scope impact is consensus flaw, transaction manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: an off-by-one epoch or feature gate edge can make this code validate user-controlled state transitions under the wrong protocol parameters, violating the actual protocol invariant that epoch-specific protocol parameters, shard layouts, validators, and feature gates are applied consistently for every block height.

## Target

- File/function: core/primitives/src/types/chunk_validator_stats.rs:55::produced
- Entrypoint: public RPC transaction/query input decoded into core/primitives protocol objects
- User-controlled input: transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth
- Attack path: User controls transactions and contract calls submitted around epoch boundaries, protocol feature activation heights, and resharding-triggering state growth -> public entrypoint reaches `produced` -> epoch finalization, validator/assignment lookup, protocol feature gating, and block/chunk validation handles the value -> invariant failure could produce consensus flaw, transaction manipulation, or state desynchronization
- Security invariant: epoch-specific protocol parameters, shard layouts, validators, and feature gates are applied consistently for every block height
- Expected bounty impact: consensus flaw, transaction manipulation, or state desynchronization
- Fast validation approach: run boundary-height tests that submit user transactions before/at/after epoch changes and compare validation paths, shard layout, gas costs, and state roots

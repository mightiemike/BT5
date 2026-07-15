# Q15498: get tracked shards for non validator in epoch serialization canonicality split

## Question

What can an unprivileged user do by creating accounts, storage growth, receipts, and transactions around epoch and protocol-version boundaries so that `get_tracked_shards_for_non_validator_in_epoch` in `chain/epoch-manager/src/shard_tracker.rs` processes Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values along the epoch manager and shard layout selection path? User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> `get_tracked_shards_for_non_validator_in_epoch` processes that value during RPC decoding, primitive conversion, block/chunk validation, and state transition serialization -> the all nodes decode, validate, hash, and execute one canonical representation for the same protocol object invariant might break -> potential in-scope impact is consensus flaw, transaction manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a non-canonical encoded user object can be hashed or validated differently from how it is executed, causing divergent state or authorization decisions, violating the actual protocol invariant that all nodes decode, validate, hash, and execute one canonical representation for the same protocol object.

## Target

- File/function: chain/epoch-manager/src/shard_tracker.rs:556::get_tracked_shards_for_non_validator_in_epoch
- Entrypoint: transaction effects observed across epoch boundaries through chain/epoch-manager
- User-controlled input: Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values
- Attack path: User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> public entrypoint reaches `get_tracked_shards_for_non_validator_in_epoch` -> RPC decoding, primitive conversion, block/chunk validation, and state transition serialization handles the value -> invariant failure could produce consensus flaw, transaction manipulation, or state desynchronization
- Security invariant: all nodes decode, validate, hash, and execute one canonical representation for the same protocol object
- Expected bounty impact: consensus flaw, transaction manipulation, or state desynchronization
- Fast validation approach: generate alternate encodings and edge-sized fields, then compare hashes, validation errors, execution outcomes, and state roots across full validation paths

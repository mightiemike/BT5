# Q12396: block until all instances are closed serialization canonicality split

## Question

What can an unprivileged user do by writing contract storage, creating/deleting accounts, and generating state/proof boundary cases through valid transactions so that `block_until_all_instances_are_closed` in `core/store/src/db/rocksdb/instance_tracker.rs` (impl State) processes Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values along the trie, flat storage, state sync, and proofs path? User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> `block_until_all_instances_are_closed` processes that value during RPC decoding, primitive conversion, block/chunk validation, and state transition serialization -> the all nodes decode, validate, hash, and execute one canonical representation for the same protocol object invariant might break -> potential in-scope impact is consensus flaw, transaction manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a non-canonical encoded user object can be hashed or validated differently from how it is executed, causing divergent state or authorization decisions, violating the actual protocol invariant that all nodes decode, validate, hash, and execute one canonical representation for the same protocol object.

## Target

- File/function: core/store/src/db/rocksdb/instance_tracker.rs:105::block_until_all_instances_are_closed
- Entrypoint: contract storage and account actions committed through Runtime::apply into core/store trie and flat-state paths
- User-controlled input: Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values
- Attack path: User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> public entrypoint reaches `block_until_all_instances_are_closed` -> RPC decoding, primitive conversion, block/chunk validation, and state transition serialization handles the value -> invariant failure could produce consensus flaw, transaction manipulation, or state desynchronization
- Security invariant: all nodes decode, validate, hash, and execute one canonical representation for the same protocol object
- Expected bounty impact: consensus flaw, transaction manipulation, or state desynchronization
- Fast validation approach: generate alternate encodings and edge-sized fields, then compare hashes, validation errors, execution outcomes, and state roots across full validation paths

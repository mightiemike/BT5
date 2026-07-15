# Q14897: run serialization canonicality split

## Question

What can an unprivileged user do by deploying WASM bytecode and invoking exported contract methods with chosen arguments so that `run` in `runtime/near-vm-runner/src/runner.rs` processes Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values along the WASM preparation and execution path? User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> `run` processes that value during RPC decoding, primitive conversion, block/chunk validation, and state transition serialization -> the all nodes decode, validate, hash, and execute one canonical representation for the same protocol object invariant might break -> potential in-scope impact is consensus flaw, transaction manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a non-canonical encoded user object can be hashed or validated differently from how it is executed, causing divergent state or authorization decisions, violating the actual protocol invariant that all nodes decode, validate, hash, and execute one canonical representation for the same protocol object.

## Target

- File/function: runtime/near-vm-runner/src/runner.rs:89::run
- Entrypoint: contract deployment and function call executed through runtime/near-vm-runner/src/runner.rs::run
- User-controlled input: Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values
- Attack path: User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> public entrypoint reaches `run` -> RPC decoding, primitive conversion, block/chunk validation, and state transition serialization handles the value -> invariant failure could produce consensus flaw, transaction manipulation, or state desynchronization
- Security invariant: all nodes decode, validate, hash, and execute one canonical representation for the same protocol object
- Expected bounty impact: consensus flaw, transaction manipulation, or state desynchronization
- Fast validation approach: generate alternate encodings and edge-sized fields, then compare hashes, validation errors, execution outcomes, and state roots across full validation paths

# Q12090: pack serialization canonicality split

## Question

What can an unprivileged user do by choosing signatures, public keys, delegate-action wrappers, signed payload fields, and encoded transaction bytes so that `pack` in `core/crypto/src/util.rs` (impl Packable for [u8; 32]) processes Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values along the signature, hash, and authorization verification path? User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> `pack` processes that value during RPC decoding, primitive conversion, block/chunk validation, and state transition serialization -> the all nodes decode, validate, hash, and execute one canonical representation for the same protocol object invariant might break -> potential in-scope impact is consensus flaw, transaction manipulation, or state desynchronization under the NEAR HackenProof scope. Exploit hypothesis: a non-canonical encoded user object can be hashed or validated differently from how it is executed, causing divergent state or authorization decisions, violating the actual protocol invariant that all nodes decode, validate, hash, and execute one canonical representation for the same protocol object.

## Target

- File/function: core/crypto/src/util.rs:39::pack
- Entrypoint: signed transaction or delegated action submitted through public RPC
- User-controlled input: Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values
- Attack path: User controls Borsh/JSON/protobuf encoded transactions, receipts, proofs, account IDs, and numeric boundary values -> public entrypoint reaches `pack` -> RPC decoding, primitive conversion, block/chunk validation, and state transition serialization handles the value -> invariant failure could produce consensus flaw, transaction manipulation, or state desynchronization
- Security invariant: all nodes decode, validate, hash, and execute one canonical representation for the same protocol object
- Expected bounty impact: consensus flaw, transaction manipulation, or state desynchronization
- Fast validation approach: generate alternate encodings and edge-sized fields, then compare hashes, validation errors, execution outcomes, and state roots across full validation paths

# Q15135: send tx commit signature domain binding

## Question

What can an unprivileged user do by calling a public RPC method or submitting a signed transaction through broadcast_tx_* or query endpoints so that `send_tx_commit` in `chain/jsonrpc/src/lib.rs` processes public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes along the RPC validation and forwarding path? User controls public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes -> `send_tx_commit` processes that value during signature parsing, domain separation, action validation, and authorization checks -> the signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed invariant might break -> potential in-scope impact is unauthorized transaction or cryptographic flaw under the NEAR HackenProof scope. Exploit hypothesis: a serialization or domain-separation mismatch can make this code verify a signature for a payload different from the one applied to state, violating the actual protocol invariant that signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed.

## Target

- File/function: chain/jsonrpc/src/lib.rs:1061::send_tx_commit
- Entrypoint: public JSON-RPC request handled by chain/jsonrpc/src/lib.rs::JsonRpcHandler::process
- User-controlled input: public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes
- Attack path: User controls public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes -> public entrypoint reaches `send_tx_commit` -> signature parsing, domain separation, action validation, and authorization checks handles the value -> invariant failure could produce unauthorized transaction or cryptographic flaw
- Security invariant: signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed
- Expected bounty impact: unauthorized transaction or cryptographic flaw
- Fast validation approach: mutate signed payload fields, delegate wrappers, encoding variants, and account/receiver bindings while checking that every non-identical payload fails authorization

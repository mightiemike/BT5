# Q15559: start http for readonly debug querying signature domain binding

## Question

What can an unprivileged user do by calling a public RPC method or submitting a signed transaction through broadcast_tx_* or query endpoints so that `start_http_for_readonly_debug_querying` in `chain/jsonrpc/src/lib.rs` processes public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes along the RPC validation and forwarding path? User controls public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes -> `start_http_for_readonly_debug_querying` processes that value during signature parsing, domain separation, action validation, and authorization checks -> the signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed invariant might break -> potential in-scope impact is unauthorized transaction or cryptographic flaw under the NEAR HackenProof scope. Exploit hypothesis: a serialization or domain-separation mismatch can make this code verify a signature for a payload different from the one applied to state, violating the actual protocol invariant that signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed.

## Target

- File/function: chain/jsonrpc/src/lib.rs:3120::start_http_for_readonly_debug_querying
- Entrypoint: public JSON-RPC request handled by chain/jsonrpc/src/lib.rs::JsonRpcHandler::process
- User-controlled input: public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes
- Attack path: User controls public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes -> public entrypoint reaches `start_http_for_readonly_debug_querying` -> signature parsing, domain separation, action validation, and authorization checks handles the value -> invariant failure could produce unauthorized transaction or cryptographic flaw
- Security invariant: signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed
- Expected bounty impact: unauthorized transaction or cryptographic flaw
- Fast validation approach: mutate signed payload fields, delegate wrappers, encoding variants, and account/receiver bindings while checking that every non-identical payload fails authorization

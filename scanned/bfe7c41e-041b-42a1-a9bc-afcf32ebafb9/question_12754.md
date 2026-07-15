# Q12754: verify nonce signature domain binding

## Question

What can an unprivileged user do by sending standard client-visible requests and transaction propagation messages without validator privileges so that `verify_nonce` in `chain/network/src/routing/edge.rs` processes public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes along the public networking and message routing path? User controls public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes -> `verify_nonce` processes that value during signature parsing, domain separation, action validation, and authorization checks -> the signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed invariant might break -> potential in-scope impact is unauthorized transaction or cryptographic flaw under the NEAR HackenProof scope. Exploit hypothesis: a serialization or domain-separation mismatch can make this code verify a signature for a payload different from the one applied to state, violating the actual protocol invariant that signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed.

## Target

- File/function: chain/network/src/routing/edge.rs:20::verify_nonce
- Entrypoint: ordinary public network/RPC transaction propagation into chain/network and client actors
- User-controlled input: public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes
- Attack path: User controls public keys, signatures, transaction bodies, delegate-action payloads, chain IDs, and encoded block hashes -> public entrypoint reaches `verify_nonce` -> signature parsing, domain separation, action validation, and authorization checks handles the value -> invariant failure could produce unauthorized transaction or cryptographic flaw
- Security invariant: signatures authorize exactly the serialized payload, account, permission, chain context, and nonce being executed
- Expected bounty impact: unauthorized transaction or cryptographic flaw
- Fast validation approach: mutate signed payload fields, delegate wrappers, encoding variants, and account/receiver bindings while checking that every non-identical payload fails authorization

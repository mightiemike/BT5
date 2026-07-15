# Q12059: hash to scalar nonce and replay boundary

## Question

What can an unprivileged user do by choosing signatures, public keys, delegate-action wrappers, signed payload fields, and encoded transaction bytes so that `_hash_to_scalar` in `core/crypto/src/hash.rs` (impl Hashable for Scalar, module hashable_trait) processes signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing along the signature, hash, and authorization verification path? User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> `_hash_to_scalar` processes that value during RPC admission, transaction pool selection, runtime verification, and access-key update -> the each valid signature/nonce pair is accepted once and only while its block hash is within the validity window invariant might break -> potential in-scope impact is unauthorized transaction, replay, or transaction manipulation under the NEAR HackenProof scope. Exploit hypothesis: a timing or nonce-gap edge case can make this code accept a replayed or expired user transaction without the intended access-key state transition, violating the actual protocol invariant that each valid signature/nonce pair is accepted once and only while its block hash is within the validity window.

## Target

- File/function: core/crypto/src/hash.rs:76::_hash_to_scalar
- Entrypoint: signed transaction or delegated action submitted through public RPC
- User-controlled input: signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing
- Attack path: User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> public entrypoint reaches `_hash_to_scalar` -> RPC admission, transaction pool selection, runtime verification, and access-key update handles the value -> invariant failure could produce unauthorized transaction, replay, or transaction manipulation
- Security invariant: each valid signature/nonce pair is accepted once and only while its block hash is within the validity window
- Expected bounty impact: unauthorized transaction, replay, or transaction manipulation
- Fast validation approach: submit same-account transactions and delegate actions across nonce gaps, forks, and expiration heights, then assert exactly-one acceptance and monotonic access-key nonce updates

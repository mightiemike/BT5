# Q13919: from addr nonce and replay boundary

## Question

What can an unprivileged user do by sending standard client-visible requests and transaction propagation messages without validator privileges so that `from_addr` in `chain/network/src/blacklist.rs` (impl Entry) processes signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing along the public networking and message routing path? User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> `from_addr` processes that value during RPC admission, transaction pool selection, runtime verification, and access-key update -> the each valid signature/nonce pair is accepted once and only while its block hash is within the validity window invariant might break -> potential in-scope impact is unauthorized transaction, replay, or transaction manipulation under the NEAR HackenProof scope. Exploit hypothesis: a timing or nonce-gap edge case can make this code accept a replayed or expired user transaction without the intended access-key state transition, violating the actual protocol invariant that each valid signature/nonce pair is accepted once and only while its block hash is within the validity window.

## Target

- File/function: chain/network/src/blacklist.rs:24::from_addr
- Entrypoint: ordinary public network/RPC transaction propagation into chain/network and client actors
- User-controlled input: signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing
- Attack path: User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> public entrypoint reaches `from_addr` -> RPC admission, transaction pool selection, runtime verification, and access-key update handles the value -> invariant failure could produce unauthorized transaction, replay, or transaction manipulation
- Security invariant: each valid signature/nonce pair is accepted once and only while its block hash is within the validity window
- Expected bounty impact: unauthorized transaction, replay, or transaction manipulation
- Fast validation approach: submit same-account transactions and delegate actions across nonce gaps, forks, and expiration heights, then assert exactly-one acceptance and monotonic access-key nonce updates

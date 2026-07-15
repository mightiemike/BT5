# Q12533: add regular key nonce and replay boundary

## Question

What can an unprivileged user do by submitting transactions, deploying contracts, calling methods, and creating promise receipts so that `add_regular_key` in `runtime/runtime/src/access_keys.rs` processes signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing along the runtime state transition path? User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> `add_regular_key` processes that value during RPC admission, transaction pool selection, runtime verification, and access-key update -> the each valid signature/nonce pair is accepted once and only while its block hash is within the validity window invariant might break -> potential in-scope impact is unauthorized transaction, replay, or transaction manipulation under the NEAR HackenProof scope. Exploit hypothesis: a timing or nonce-gap edge case can make this code accept a replayed or expired user transaction without the intended access-key state transition, violating the actual protocol invariant that each valid signature/nonce pair is accepted once and only while its block hash is within the validity window.

## Target

- File/function: runtime/runtime/src/access_keys.rs:230::add_regular_key
- Entrypoint: signed transaction submitted through public RPC and applied by runtime/runtime/src/lib.rs::Runtime::apply
- User-controlled input: signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing
- Attack path: User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> public entrypoint reaches `add_regular_key` -> RPC admission, transaction pool selection, runtime verification, and access-key update handles the value -> invariant failure could produce unauthorized transaction, replay, or transaction manipulation
- Security invariant: each valid signature/nonce pair is accepted once and only while its block hash is within the validity window
- Expected bounty impact: unauthorized transaction, replay, or transaction manipulation
- Fast validation approach: submit same-account transactions and delegate actions across nonce gaps, forks, and expiration heights, then assert exactly-one acceptance and monotonic access-key nonce updates

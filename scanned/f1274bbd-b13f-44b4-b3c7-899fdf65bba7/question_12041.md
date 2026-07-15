# Q12041: report wait until metric nonce and replay boundary

## Question

What can an unprivileged user do by calling a public RPC method or submitting a signed transaction through broadcast_tx_* or query endpoints so that `report_wait_until_metric` in `chain/jsonrpc/src/metrics.rs` processes signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing along the RPC validation and forwarding path? User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> `report_wait_until_metric` processes that value during RPC admission, transaction pool selection, runtime verification, and access-key update -> the each valid signature/nonce pair is accepted once and only while its block hash is within the validity window invariant might break -> potential in-scope impact is unauthorized transaction, replay, or transaction manipulation under the NEAR HackenProof scope. Exploit hypothesis: a timing or nonce-gap edge case can make this code accept a replayed or expired user transaction without the intended access-key state transition, violating the actual protocol invariant that each valid signature/nonce pair is accepted once and only while its block hash is within the validity window.

## Target

- File/function: chain/jsonrpc/src/metrics.rs:68::report_wait_until_metric
- Entrypoint: public JSON-RPC request handled by chain/jsonrpc/src/lib.rs::JsonRpcHandler::process
- User-controlled input: signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing
- Attack path: User controls signed transactions, delegated actions, access-key nonce values, recent block hashes, and expiration timing -> public entrypoint reaches `report_wait_until_metric` -> RPC admission, transaction pool selection, runtime verification, and access-key update handles the value -> invariant failure could produce unauthorized transaction, replay, or transaction manipulation
- Security invariant: each valid signature/nonce pair is accepted once and only while its block hash is within the validity window
- Expected bounty impact: unauthorized transaction, replay, or transaction manipulation
- Fast validation approach: submit same-account transactions and delegate actions across nonce gaps, forks, and expiration heights, then assert exactly-one acceptance and monotonic access-key nonce updates

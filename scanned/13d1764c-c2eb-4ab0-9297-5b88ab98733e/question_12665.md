# Q12665: pool eviction safety gap in client::pool_for_shard

## Question
Can an unprivileged attacker submit conflicting transactions that pressure mempool admission and eviction that reaches `chain/chunks/src/client.rs::pool_for_shard` with control over fee, nonce, and balance relationships among attacker-controlled transactions and make nearcore keep a transaction executable after the state that justified its admission is gone, breaking the invariant that pool eviction and revalidation must prevent stale or conflicting transactions from surviving to execution, and leading to unauthorized transaction?

## Target
- File/function: `chain/chunks/src/client.rs::pool_for_shard`
- Entrypoint: submit conflicting transactions that pressure mempool admission and eviction
- Attacker controls: fee, nonce, and balance relationships among attacker-controlled transactions
- Exploit idea: keep a transaction executable after the state that justified its admission is gone
- Invariant to test: pool eviction and revalidation must prevent stale or conflicting transactions from surviving to execution
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a mempool pressure test that changes balances or nonces after admission and assert stale transactions are evicted or revalidated

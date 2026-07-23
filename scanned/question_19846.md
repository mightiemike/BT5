# Q19846: cross-shard auth replay in client::update_validator_signer

## Question
Can an unprivileged attacker submit a cross-shard transaction with callbacks that reaches `chain/client/src/client.rs::update_validator_signer` with control over receipt order, callback targets, and retry timing across shards and make nearcore let authorization survive past the one cross-shard execution slot it was meant to cover, breaking the invariant that cross-shard callbacks must not recreate or replay user authorization, and leading to unauthorized transaction?

## Target
- File/function: `chain/client/src/client.rs::update_validator_signer`
- Entrypoint: submit a cross-shard transaction with callbacks
- Attacker controls: receipt order, callback targets, and retry timing across shards
- Exploit idea: let authorization survive past the one cross-shard execution slot it was meant to cover
- Invariant to test: cross-shard callbacks must not recreate or replay user authorization
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a cross-shard callback replay test and assert no downstream receipt can execute with replayed authority

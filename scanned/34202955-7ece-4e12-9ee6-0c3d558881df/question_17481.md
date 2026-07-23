# Q17481: chain-context binding omission in cache::config_cache_key_signature

## Question
Can an unprivileged attacker submit a transaction near a protocol, epoch, or shard boundary that reaches `runtime/near-vm-runner/src/cache.rs::config_cache_key_signature` with control over the same signed payload timed around a user-reachable boundary change and make nearcore validate authorization without fully binding the transaction to the chain context that will execute it, breaking the invariant that authorization must stay bound to the exact chain context in which execution occurs, and leading to unauthorized transaction?

## Target
- File/function: `runtime/near-vm-runner/src/cache.rs::config_cache_key_signature`
- Entrypoint: submit a transaction near a protocol, epoch, or shard boundary
- Attacker controls: the same signed payload timed around a user-reachable boundary change
- Exploit idea: validate authorization without fully binding the transaction to the chain context that will execute it
- Invariant to test: authorization must stay bound to the exact chain context in which execution occurs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a boundary test that advances the chain context between validation and execution and assert the payload is revalidated or rejected

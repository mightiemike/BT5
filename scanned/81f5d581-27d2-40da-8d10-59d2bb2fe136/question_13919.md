# Q13919: account creation authority bleed in cache::config_cache_key_signature

## Question
Can an unprivileged attacker submit create-account or add-key style transactions that reaches `runtime/near-vm-runner/src/cache.rs::config_cache_key_signature` with control over new account ids, key sets, and ordering against related transactions and make nearcore carry authority from a previous account or key state into a newly created or rotated one, breaking the invariant that account creation and key rotation must fully replace prior authority boundaries, and leading to unauthorized transaction?

## Target
- File/function: `runtime/near-vm-runner/src/cache.rs::config_cache_key_signature`
- Entrypoint: submit create-account or add-key style transactions
- Attacker controls: new account ids, key sets, and ordering against related transactions
- Exploit idea: carry authority from a previous account or key state into a newly created or rotated one
- Invariant to test: account creation and key rotation must fully replace prior authority boundaries
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a create-account plus rotate-key scenario and assert old authority cannot authorize the new state

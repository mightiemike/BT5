# Q6795: signature context drift in cache::config_cache_key_signature

## Question
Can an unprivileged attacker submit a signed transaction through default-enabled RPC or mempool admission that reaches `runtime/near-vm-runner/src/cache.rs::config_cache_key_signature` with control over serialized transaction bytes that preserve one signature but alter surrounding signed context and make nearcore accept a signature that is valid for one transaction image while executing another logical transaction, breaking the invariant that signature verification must bind every executed field of the transaction image, and leading to cryptographic flaws?

## Target
- File/function: `runtime/near-vm-runner/src/cache.rs::config_cache_key_signature`
- Entrypoint: submit a signed transaction through default-enabled RPC or mempool admission
- Attacker controls: serialized transaction bytes that preserve one signature but alter surrounding signed context
- Exploit idea: accept a signature that is valid for one transaction image while executing another logical transaction
- Invariant to test: signature verification must bind every executed field of the transaction image
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a serialization test that keeps the same signature while mutating a bound field and assert validation rejects the mutation

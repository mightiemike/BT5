# Q16091: replacement semantics mutation in pending::PendingBlocksPool

## Question
Can an unprivileged attacker submit a replacement transaction that should supersede an older one that reaches `chain/chain/src/pending.rs::PendingBlocksPool` with control over old and new transaction pairs with carefully chosen fee and nonce fields and make nearcore mutate the supersession boundary so both transactions can influence execution state, breaking the invariant that replacement logic must leave one canonical surviving transaction per signer and nonce, and leading to transaction manipulation?

## Target
- File/function: `chain/chain/src/pending.rs::PendingBlocksPool`
- Entrypoint: submit a replacement transaction that should supersede an older one
- Attacker controls: old and new transaction pairs with carefully chosen fee and nonce fields
- Exploit idea: mutate the supersession boundary so both transactions can influence execution state
- Invariant to test: replacement logic must leave one canonical surviving transaction per signer and nonce
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a replacement test with adversarial fee and nonce pairs and assert only one transaction remains executable

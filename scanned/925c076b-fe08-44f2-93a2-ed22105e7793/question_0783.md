# Q783: nonce authorization split in transaction::signer_id

## Question
Can an unprivileged attacker submit a signed transaction or delegate action that reaches `core/primitives/src/transaction.rs::signer_id` with control over a valid signature together with a crafted nonce, receiver, and action list and make nearcore bind authorization checks to one nonce or action context and downstream execution to another, breaking the invariant that one signed payload authorizes exactly one nonce, signer, receiver, and action sequence, and leading to unauthorized transaction?

## Target
- File/function: `core/primitives/src/transaction.rs::signer_id`
- Entrypoint: submit a signed transaction or delegate action
- Attacker controls: a valid signature together with a crafted nonce, receiver, and action list
- Exploit idea: bind authorization checks to one nonce or action context and downstream execution to another
- Invariant to test: one signed payload authorizes exactly one nonce, signer, receiver, and action sequence
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a transaction-level integration test that reuses one signature context across two action encodings and assert the second path is rejected before any receipt is created

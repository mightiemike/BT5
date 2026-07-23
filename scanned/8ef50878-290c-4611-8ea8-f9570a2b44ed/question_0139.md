# Q139: nonce authorization split in signature_verification::verify_block_header_signature_with_epoch_manager

## Question
Can an unprivileged attacker submit a signed transaction or delegate action that reaches `chain/chain/src/signature_verification.rs::verify_block_header_signature_with_epoch_manager` with control over a valid signature together with a crafted nonce, receiver, and action list and make nearcore bind authorization checks to one nonce or action context and downstream execution to another, breaking the invariant that one signed payload authorizes exactly one nonce, signer, receiver, and action sequence, and leading to unauthorized transaction?

## Target
- File/function: `chain/chain/src/signature_verification.rs::verify_block_header_signature_with_epoch_manager`
- Entrypoint: submit a signed transaction or delegate action
- Attacker controls: a valid signature together with a crafted nonce, receiver, and action list
- Exploit idea: bind authorization checks to one nonce or action context and downstream execution to another
- Invariant to test: one signed payload authorizes exactly one nonce, signer, receiver, and action sequence
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a transaction-level integration test that reuses one signature context across two action encodings and assert the second path is rejected before any receipt is created

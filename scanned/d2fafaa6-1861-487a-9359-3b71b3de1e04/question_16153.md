# Q16153: chain-context binding omission in signer_overlay::SignerOverlay

## Question
Can an unprivileged attacker submit a transaction near a protocol, epoch, or shard boundary that reaches `chain/chain/src/runtime/signer_overlay.rs::SignerOverlay` with control over the same signed payload timed around a user-reachable boundary change and make nearcore validate authorization without fully binding the transaction to the chain context that will execute it, breaking the invariant that authorization must stay bound to the exact chain context in which execution occurs, and leading to unauthorized transaction?

## Target
- File/function: `chain/chain/src/runtime/signer_overlay.rs::SignerOverlay`
- Entrypoint: submit a transaction near a protocol, epoch, or shard boundary
- Attacker controls: the same signed payload timed around a user-reachable boundary change
- Exploit idea: validate authorization without fully binding the transaction to the chain context that will execute it
- Invariant to test: authorization must stay bound to the exact chain context in which execution occurs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a boundary test that advances the chain context between validation and execution and assert the payload is revalidated or rejected

# Q16563: chain-context binding omission in block::verify_total_supply_checked_spice

## Question
Can an unprivileged attacker submit a transaction near a protocol, epoch, or shard boundary that reaches `core/primitives/src/block.rs::verify_total_supply_checked_spice` with control over the same signed payload timed around a user-reachable boundary change and make nearcore validate authorization without fully binding the transaction to the chain context that will execute it, breaking the invariant that authorization must stay bound to the exact chain context in which execution occurs, and leading to unauthorized transaction?

## Target
- File/function: `core/primitives/src/block.rs::verify_total_supply_checked_spice`
- Entrypoint: submit a transaction near a protocol, epoch, or shard boundary
- Attacker controls: the same signed payload timed around a user-reachable boundary change
- Exploit idea: validate authorization without fully binding the transaction to the chain context that will execute it
- Invariant to test: authorization must stay bound to the exact chain context in which execution occurs
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a boundary test that advances the chain context between validation and execution and assert the payload is revalidated or rejected

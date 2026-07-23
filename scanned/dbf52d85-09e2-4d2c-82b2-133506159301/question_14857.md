# Q14857: protocol-boundary staking drift in epoch_manager::shard_layout_config

## Question
Can an unprivileged attacker submit stake transactions around a protocol feature transition that reaches `core/primitives/src/epoch_manager.rs::shard_layout_config` with control over stake state that exercises both old and new staking logic at the boundary and make nearcore apply two different staking invariants to the same account state around the transition, breaking the invariant that protocol transitions must preserve one canonical staking interpretation for every account, and leading to consensus flaws?

## Target
- File/function: `core/primitives/src/epoch_manager.rs::shard_layout_config`
- Entrypoint: submit stake transactions around a protocol feature transition
- Attacker controls: stake state that exercises both old and new staking logic at the boundary
- Exploit idea: apply two different staking invariants to the same account state around the transition
- Invariant to test: protocol transitions must preserve one canonical staking interpretation for every account
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a protocol-transition staking test and assert balances and validator views remain consistent across the boundary

# Q14547: protocol-boundary staking drift in adapter::get_epoch_start_from_epoch_id

## Question
Can an unprivileged attacker submit stake transactions around a protocol feature transition that reaches `chain/epoch-manager/src/adapter.rs::get_epoch_start_from_epoch_id` with control over stake state that exercises both old and new staking logic at the boundary and make nearcore apply two different staking invariants to the same account state around the transition, breaking the invariant that protocol transitions must preserve one canonical staking interpretation for every account, and leading to consensus flaws?

## Target
- File/function: `chain/epoch-manager/src/adapter.rs::get_epoch_start_from_epoch_id`
- Entrypoint: submit stake transactions around a protocol feature transition
- Attacker controls: stake state that exercises both old and new staking logic at the boundary
- Exploit idea: apply two different staking invariants to the same account state around the transition
- Invariant to test: protocol transitions must preserve one canonical staking interpretation for every account
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a protocol-transition staking test and assert balances and validator views remain consistent across the boundary

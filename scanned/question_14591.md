# Q14591: protocol-boundary staking drift in validator_selection::partial_cmp

## Question
Can an unprivileged attacker submit stake transactions around a protocol feature transition that reaches `chain/epoch-manager/src/validator_selection.rs::partial_cmp` with control over stake state that exercises both old and new staking logic at the boundary and make nearcore apply two different staking invariants to the same account state around the transition, breaking the invariant that protocol transitions must preserve one canonical staking interpretation for every account, and leading to consensus flaws?

## Target
- File/function: `chain/epoch-manager/src/validator_selection.rs::partial_cmp`
- Entrypoint: submit stake transactions around a protocol feature transition
- Attacker controls: stake state that exercises both old and new staking logic at the boundary
- Exploit idea: apply two different staking invariants to the same account state around the transition
- Invariant to test: protocol transitions must preserve one canonical staking interpretation for every account
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a protocol-transition staking test and assert balances and validator views remain consistent across the boundary

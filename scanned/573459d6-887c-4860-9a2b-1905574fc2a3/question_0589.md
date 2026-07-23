# Q589: epoch-boundary stake accounting in epoch_block_info::last_finalized_height

## Question
Can an unprivileged attacker submit stake or unstake transactions near an epoch transition that reaches `core/primitives/src/epoch_block_info.rs::last_finalized_height` with control over stake amount, timing around the epoch boundary, and related balance-moving transactions and make nearcore apply locked and unlocked balance updates in different epoch views, breaking the invariant that staking balance, locked balance, and liquid balance must reconcile exactly across epoch transitions, and leading to balance manipulation?

## Target
- File/function: `core/primitives/src/epoch_block_info.rs::last_finalized_height`
- Entrypoint: submit stake or unstake transactions near an epoch transition
- Attacker controls: stake amount, timing around the epoch boundary, and related balance-moving transactions
- Exploit idea: apply locked and unlocked balance updates in different epoch views
- Invariant to test: staking balance, locked balance, and liquid balance must reconcile exactly across epoch transitions
- Expected Immunefi impact: Balance manipulation
- Fast validation: write an epoch-boundary staking test and assert locked and liquid balances reconcile before and after the transition

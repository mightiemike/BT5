# Q323: epoch-boundary stake accounting in sticky_resharding::bin_pack_into_children

## Question
Can an unprivileged attacker submit stake or unstake transactions near an epoch transition that reaches `chain/epoch-manager/src/shard_assignment/sticky_resharding.rs::bin_pack_into_children` with control over stake amount, timing around the epoch boundary, and related balance-moving transactions and make nearcore apply locked and unlocked balance updates in different epoch views, breaking the invariant that staking balance, locked balance, and liquid balance must reconcile exactly across epoch transitions, and leading to balance manipulation?

## Target
- File/function: `chain/epoch-manager/src/shard_assignment/sticky_resharding.rs::bin_pack_into_children`
- Entrypoint: submit stake or unstake transactions near an epoch transition
- Attacker controls: stake amount, timing around the epoch boundary, and related balance-moving transactions
- Exploit idea: apply locked and unlocked balance updates in different epoch views
- Invariant to test: staking balance, locked balance, and liquid balance must reconcile exactly across epoch transitions
- Expected Immunefi impact: Balance manipulation
- Fast validation: write an epoch-boundary staking test and assert locked and liquid balances reconcile before and after the transition

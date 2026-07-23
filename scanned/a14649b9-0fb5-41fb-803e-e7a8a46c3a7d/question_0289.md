# Q289: epoch-boundary stake accounting in rpc_handler::is_chunk_producer_for_transaction_in_epoch

## Question
Can an unprivileged attacker submit stake or unstake transactions near an epoch transition that reaches `chain/client/src/rpc_handler.rs::is_chunk_producer_for_transaction_in_epoch` with control over stake amount, timing around the epoch boundary, and related balance-moving transactions and make nearcore apply locked and unlocked balance updates in different epoch views, breaking the invariant that staking balance, locked balance, and liquid balance must reconcile exactly across epoch transitions, and leading to balance manipulation?

## Target
- File/function: `chain/client/src/rpc_handler.rs::is_chunk_producer_for_transaction_in_epoch`
- Entrypoint: submit stake or unstake transactions near an epoch transition
- Attacker controls: stake amount, timing around the epoch boundary, and related balance-moving transactions
- Exploit idea: apply locked and unlocked balance updates in different epoch views
- Invariant to test: staking balance, locked balance, and liquid balance must reconcile exactly across epoch transitions
- Expected Immunefi impact: Balance manipulation
- Fast validation: write an epoch-boundary staking test and assert locked and liquid balances reconcile before and after the transition

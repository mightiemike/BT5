# Q5408: epoch-boundary accounting drift in pending::contains_key

## Question
Can an unprivileged attacker submit stake, unstake, or ordinary transactions near an epoch transition that reaches `chain/chain/src/pending.rs::contains_key` with control over timing around the epoch boundary and interactions between account balances and staking state and make nearcore advance one epoch-facing view of balances or stake while another view lags behind, breaking the invariant that epoch transitions must consume one canonical balance and staking view for all accepted transactions, and leading to balance manipulation?

## Target
- File/function: `chain/chain/src/pending.rs::contains_key`
- Entrypoint: submit stake, unstake, or ordinary transactions near an epoch transition
- Attacker controls: timing around the epoch boundary and interactions between account balances and staking state
- Exploit idea: advance one epoch-facing view of balances or stake while another view lags behind
- Invariant to test: epoch transitions must consume one canonical balance and staking view for all accepted transactions
- Expected Immunefi impact: Balance manipulation
- Fast validation: write an epoch-boundary test that compares pre- and post-transition balances, locks, and validator views

# Q11016: epoch-info representation skew in shard_tracker::cares_about_shard_in_epoch_from_prev_hash

## Question
Can an unprivileged attacker submit stake updates that change validator-set composition that reaches `chain/epoch-manager/src/shard_tracker.rs::cares_about_shard_in_epoch_from_prev_hash` with control over stake distribution and ordering across attacker-controlled accounts and make nearcore derive epoch-facing validator information from representation details rather than canonical stake content, breaking the invariant that epoch info and validator-set derivation must be canonical for one accepted stake set, and leading to consensus flaws?

## Target
- File/function: `chain/epoch-manager/src/shard_tracker.rs::cares_about_shard_in_epoch_from_prev_hash`
- Entrypoint: submit stake updates that change validator-set composition
- Attacker controls: stake distribution and ordering across attacker-controlled accounts
- Exploit idea: derive epoch-facing validator information from representation details rather than canonical stake content
- Invariant to test: epoch info and validator-set derivation must be canonical for one accepted stake set
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a validator-set derivation test that permutes equivalent stake updates and assert identical epoch info

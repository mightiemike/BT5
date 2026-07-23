# Q5681: unstake lock-rule bypass in shard_tracker::tracks_shard_prev_epoch_from_prev_block

## Question
Can an unprivileged attacker submit unstake and withdraw style transactions in rapid sequence that reaches `chain/epoch-manager/src/shard_tracker.rs::tracks_shard_prev_epoch_from_prev_block` with control over timing and amounts that stress lock-period and unlock-accounting edges and make nearcore treat stake as unlocked for withdrawal on one path while another path still considers it locked, breaking the invariant that withdrawal availability must follow one canonical unlock schedule, and leading to stealing or loss of funds?

## Target
- File/function: `chain/epoch-manager/src/shard_tracker.rs::tracks_shard_prev_epoch_from_prev_block`
- Entrypoint: submit unstake and withdraw style transactions in rapid sequence
- Attacker controls: timing and amounts that stress lock-period and unlock-accounting edges
- Exploit idea: treat stake as unlocked for withdrawal on one path while another path still considers it locked
- Invariant to test: withdrawal availability must follow one canonical unlock schedule
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write an unstake-then-withdraw timing test and assert withdrawal fails until the canonical unlock point

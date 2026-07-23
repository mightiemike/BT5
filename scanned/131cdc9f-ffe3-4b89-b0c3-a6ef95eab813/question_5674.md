# Q5674: unstake lock-rule bypass in shard_tracker::get_shadow_validator_id

## Question
Can an unprivileged attacker submit unstake and withdraw style transactions in rapid sequence that reaches `chain/epoch-manager/src/shard_tracker.rs::get_shadow_validator_id` with control over timing and amounts that stress lock-period and unlock-accounting edges and make nearcore treat stake as unlocked for withdrawal on one path while another path still considers it locked, breaking the invariant that withdrawal availability must follow one canonical unlock schedule, and leading to stealing or loss of funds?

## Target
- File/function: `chain/epoch-manager/src/shard_tracker.rs::get_shadow_validator_id`
- Entrypoint: submit unstake and withdraw style transactions in rapid sequence
- Attacker controls: timing and amounts that stress lock-period and unlock-accounting edges
- Exploit idea: treat stake as unlocked for withdrawal on one path while another path still considers it locked
- Invariant to test: withdrawal availability must follow one canonical unlock schedule
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write an unstake-then-withdraw timing test and assert withdrawal fails until the canonical unlock point

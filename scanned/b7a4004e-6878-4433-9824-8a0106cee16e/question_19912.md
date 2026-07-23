# Q19912: withdrawal queue resurrection in sticky_resharding::add_validator

## Question
Can an unprivileged attacker submit stake, unstake, and follow-up withdrawal transactions that become temporarily ineligible that reaches `chain/epoch-manager/src/shard_assignment/sticky_resharding.rs::add_validator` with control over timing and sequence of related staking actions and make nearcore resurrect a stale withdrawal right after the canonical state should have invalidated it, breaking the invariant that withdrawal rights must evolve monotonically with the canonical staking state, and leading to stealing or loss of funds?

## Target
- File/function: `chain/epoch-manager/src/shard_assignment/sticky_resharding.rs::add_validator`
- Entrypoint: submit stake, unstake, and follow-up withdrawal transactions that become temporarily ineligible
- Attacker controls: timing and sequence of related staking actions
- Exploit idea: resurrect a stale withdrawal right after the canonical state should have invalidated it
- Invariant to test: withdrawal rights must evolve monotonically with the canonical staking state
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a stale-withdrawal scenario and assert once-invalidated withdrawal rights cannot become executable without fresh valid state

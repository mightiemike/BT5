# Q11580: epoch-info representation skew in epoch_store::get_epoch_validator_info

## Question
Can an unprivileged attacker submit stake updates that change validator-set composition that reaches `core/store/src/adapter/epoch_store.rs::get_epoch_validator_info` with control over stake distribution and ordering across attacker-controlled accounts and make nearcore derive epoch-facing validator information from representation details rather than canonical stake content, breaking the invariant that epoch info and validator-set derivation must be canonical for one accepted stake set, and leading to consensus flaws?

## Target
- File/function: `core/store/src/adapter/epoch_store.rs::get_epoch_validator_info`
- Entrypoint: submit stake updates that change validator-set composition
- Attacker controls: stake distribution and ordering across attacker-controlled accounts
- Exploit idea: derive epoch-facing validator information from representation details rather than canonical stake content
- Invariant to test: epoch info and validator-set derivation must be canonical for one accepted stake set
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a validator-set derivation test that permutes equivalent stake updates and assert identical epoch info

# Q13056: reward rounding imbalance in epoch_block_info::last_finalized_height

## Question
Can an unprivileged attacker submit stake distributions that stress reward rounding boundaries that reaches `core/primitives/src/epoch_block_info.rs::last_finalized_height` with control over stake values chosen to hit rounding edges across many attacker-controlled accounts and make nearcore accumulate or discard rounding residue in a way that shifts value materially over repeated epochs, breaking the invariant that reward rounding must preserve total value and per-account fairness bounds across epochs, and leading to balance manipulation?

## Target
- File/function: `core/primitives/src/epoch_block_info.rs::last_finalized_height`
- Entrypoint: submit stake distributions that stress reward rounding boundaries
- Attacker controls: stake values chosen to hit rounding edges across many attacker-controlled accounts
- Exploit idea: accumulate or discard rounding residue in a way that shifts value materially over repeated epochs
- Invariant to test: reward rounding must preserve total value and per-account fairness bounds across epochs
- Expected Immunefi impact: Balance manipulation
- Fast validation: write a multi-epoch rounding test and assert total distributed reward matches the canonical amount exactly

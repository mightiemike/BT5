# Q14240: reward rounding imbalance in verifier::check_storage_stake

## Question
Can an unprivileged attacker submit stake distributions that stress reward rounding boundaries that reaches `runtime/runtime/src/verifier.rs::check_storage_stake` with control over stake values chosen to hit rounding edges across many attacker-controlled accounts and make nearcore accumulate or discard rounding residue in a way that shifts value materially over repeated epochs, breaking the invariant that reward rounding must preserve total value and per-account fairness bounds across epochs, and leading to balance manipulation?

## Target
- File/function: `runtime/runtime/src/verifier.rs::check_storage_stake`
- Entrypoint: submit stake distributions that stress reward rounding boundaries
- Attacker controls: stake values chosen to hit rounding edges across many attacker-controlled accounts
- Exploit idea: accumulate or discard rounding residue in a way that shifts value materially over repeated epochs
- Invariant to test: reward rounding must preserve total value and per-account fairness bounds across epochs
- Expected Immunefi impact: Balance manipulation
- Fast validation: write a multi-epoch rounding test and assert total distributed reward matches the canonical amount exactly

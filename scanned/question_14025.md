# Q14025: rollback double-apply in prepare_v3::ensure_export_section

## Question
Can an unprivileged attacker submit a transaction that partially succeeds before a later action fails that reaches `runtime/near-vm-runner/src/prepare/prepare_v3.rs::ensure_export_section` with control over action sequence, deposit amounts, and callback failure point and make nearcore commit one balance or state effect before rollback but miss the corresponding reversal on failure, breaking the invariant that failed execution branches must not leave partial credits, debits, or state writes behind, and leading to stealing or loss of funds?

## Target
- File/function: `runtime/near-vm-runner/src/prepare/prepare_v3.rs::ensure_export_section`
- Entrypoint: submit a transaction that partially succeeds before a later action fails
- Attacker controls: action sequence, deposit amounts, and callback failure point
- Exploit idea: commit one balance or state effect before rollback but miss the corresponding reversal on failure
- Invariant to test: failed execution branches must not leave partial credits, debits, or state writes behind
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a mixed-success action test and assert all balances and state keys match a clean rollback after failure

# Q12700: rollback double-apply in chunk_inclusion_tracker::prepare_chunk_headers_ready_for_inclusion

## Question
Can an unprivileged attacker submit a transaction that partially succeeds before a later action fails that reaches `chain/client/src/chunk_inclusion_tracker.rs::prepare_chunk_headers_ready_for_inclusion` with control over action sequence, deposit amounts, and callback failure point and make nearcore commit one balance or state effect before rollback but miss the corresponding reversal on failure, breaking the invariant that failed execution branches must not leave partial credits, debits, or state writes behind, and leading to stealing or loss of funds?

## Target
- File/function: `chain/client/src/chunk_inclusion_tracker.rs::prepare_chunk_headers_ready_for_inclusion`
- Entrypoint: submit a transaction that partially succeeds before a later action fails
- Attacker controls: action sequence, deposit amounts, and callback failure point
- Exploit idea: commit one balance or state effect before rollback but miss the corresponding reversal on failure
- Invariant to test: failed execution branches must not leave partial credits, debits, or state writes behind
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a mixed-success action test and assert all balances and state keys match a clean rollback after failure

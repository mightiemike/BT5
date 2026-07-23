# Q3483: duplicate application after replay in congestion_control::finalize_stats_get_outgoing_receipts

## Question
Can an unprivileged attacker resubmit a transaction or callback sequence across ordinary retry and inclusion paths that reaches `runtime/runtime/src/congestion_control.rs::finalize_stats_get_outgoing_receipts` with control over repeat timing and transaction sets that are valid under normal user behavior and make nearcore apply one transaction or receipt effect twice when the pipeline reconsiders pending work, breaking the invariant that accepted work may be reconsidered internally but applied to state at most once, and leading to balance manipulation?

## Target
- File/function: `runtime/runtime/src/congestion_control.rs::finalize_stats_get_outgoing_receipts`
- Entrypoint: resubmit a transaction or callback sequence across ordinary retry and inclusion paths
- Attacker controls: repeat timing and transaction sets that are valid under normal user behavior
- Exploit idea: apply one transaction or receipt effect twice when the pipeline reconsiders pending work
- Invariant to test: accepted work may be reconsidered internally but applied to state at most once
- Expected Immunefi impact: Balance manipulation
- Fast validation: write a repeated-inclusion scenario and assert balances, receipts, and nonces advance only once

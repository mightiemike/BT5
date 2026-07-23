# Q12546: receipt metadata replay in receipt_to_tx::resolve_receipt_via_hint

## Question
Can an unprivileged attacker submit transactions that generate many receipts and callbacks that reaches `chain/chain/src/receipt_to_tx.rs::resolve_receipt_via_hint` with control over receipt ids, callback trees, and retry timing reachable from contract execution and make nearcore reuse or resurrect metadata for a receipt that should already be consumed, breaking the invariant that each receipt identifier and routing record must transition monotonically from pending to consumed once, and leading to transaction manipulation?

## Target
- File/function: `chain/chain/src/receipt_to_tx.rs::resolve_receipt_via_hint`
- Entrypoint: submit transactions that generate many receipts and callbacks
- Attacker controls: receipt ids, callback trees, and retry timing reachable from contract execution
- Exploit idea: reuse or resurrect metadata for a receipt that should already be consumed
- Invariant to test: each receipt identifier and routing record must transition monotonically from pending to consumed once
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a receipt-consumption test that retries the same logical callback path and assert consumed metadata cannot be reused

# Q12475: receipt metadata replay in backfill_receipt_to_tx::process_one_batch

## Question
Can an unprivileged attacker submit transactions that generate many receipts and callbacks that reaches `chain/chain/src/backfill_receipt_to_tx.rs::process_one_batch` with control over receipt ids, callback trees, and retry timing reachable from contract execution and make nearcore reuse or resurrect metadata for a receipt that should already be consumed, breaking the invariant that each receipt identifier and routing record must transition monotonically from pending to consumed once, and leading to transaction manipulation?

## Target
- File/function: `chain/chain/src/backfill_receipt_to_tx.rs::process_one_batch`
- Entrypoint: submit transactions that generate many receipts and callbacks
- Attacker controls: receipt ids, callback trees, and retry timing reachable from contract execution
- Exploit idea: reuse or resurrect metadata for a receipt that should already be consumed
- Invariant to test: each receipt identifier and routing record must transition monotonically from pending to consumed once
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a receipt-consumption test that retries the same logical callback path and assert consumed metadata cannot be reused

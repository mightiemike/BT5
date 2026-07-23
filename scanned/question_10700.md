# Q10700: receipt-to-transaction misbinding in block_processing_utils::has_optimistic_block_with

## Question
Can an unprivileged attacker submit transactions that emit nested receipts and callbacks that reaches `chain/chain/src/block_processing_utils.rs::has_optimistic_block_with` with control over receipt fanout, callback timing, and receiver relationships and make nearcore bind one receipt’s accounting or authorization consequences to the wrong originating transaction, breaking the invariant that every receipt must remain attributable to exactly one originating transaction context, and leading to unauthorized transaction?

## Target
- File/function: `chain/chain/src/block_processing_utils.rs::has_optimistic_block_with`
- Entrypoint: submit transactions that emit nested receipts and callbacks
- Attacker controls: receipt fanout, callback timing, and receiver relationships
- Exploit idea: bind one receipt’s accounting or authorization consequences to the wrong originating transaction
- Invariant to test: every receipt must remain attributable to exactly one originating transaction context
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a nested-receipt scenario and assert each receipt maps back to the intended transaction for accounting and authorization

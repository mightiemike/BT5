# Q19595: cross-shard refund relocation in backfill_receipt_to_tx::add

## Question
Can an unprivileged attacker submit a cross-shard transaction that fails after producing storage-affecting receipts that reaches `chain/chain/src/backfill_receipt_to_tx.rs::add` with control over receipt destinations, refund amounts, and shard-boundary placement of the involved accounts and make nearcore move a refund record to the wrong shard or lose it while reconciling storage-owned receipt metadata, breaking the invariant that cross-shard refund records must be routed and reconciled exactly once to the intended account, and leading to stealing or loss of funds?

## Target
- File/function: `chain/chain/src/backfill_receipt_to_tx.rs::add`
- Entrypoint: submit a cross-shard transaction that fails after producing storage-affecting receipts
- Attacker controls: receipt destinations, refund amounts, and shard-boundary placement of the involved accounts
- Exploit idea: move a refund record to the wrong shard or lose it while reconciling storage-owned receipt metadata
- Invariant to test: cross-shard refund records must be routed and reconciled exactly once to the intended account
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a failing cross-shard path and assert the refund metadata and final balance end up on the intended shard and account

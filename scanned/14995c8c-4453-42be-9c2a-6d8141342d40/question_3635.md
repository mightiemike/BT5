# Q3635: resharding duplication or loss in receipt_to_tx::center_out_basic

## Question
Can an unprivileged attacker submit cross-shard transactions around a shard-layout boundary that reaches `chain/chain/src/receipt_to_tx.rs::center_out_basic` with control over account placement, receipt fanout, and timing near a user-reachable resharding transition and make nearcore copy, drop, or misroute one account state item or receipt while moving state between shard layouts, breaking the invariant that resharding must preserve every balance, receipt, and contract state item exactly once, and leading to stealing or loss of funds?

## Target
- File/function: `chain/chain/src/receipt_to_tx.rs::center_out_basic`
- Entrypoint: submit cross-shard transactions around a shard-layout boundary
- Attacker controls: account placement, receipt fanout, and timing near a user-reachable resharding transition
- Exploit idea: copy, drop, or misroute one account state item or receipt while moving state between shard layouts
- Invariant to test: resharding must preserve every balance, receipt, and contract state item exactly once
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a resharding scenario with cross-shard receipts and assert post-transition balances and receipts match a single canonical execution

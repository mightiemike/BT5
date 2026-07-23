# Q1860: flat-storage stale commit in receipt_to_tx::resolve_receipt_via_hint

## Question
Can an unprivileged attacker submit transactions that read and then overwrite the same state through normal execution that reaches `chain/chain/src/receipt_to_tx.rs::resolve_receipt_via_hint` with control over contract writes, deletions, and immediate follow-up reads across one block and make nearcore serve one state layer from a stale cache while another layer commits a newer value, breaking the invariant that flat storage, trie reads, and committed state must agree on every accepted transition, and leading to contracts execution flows?

## Target
- File/function: `chain/chain/src/receipt_to_tx.rs::resolve_receipt_via_hint`
- Entrypoint: submit transactions that read and then overwrite the same state through normal execution
- Attacker controls: contract writes, deletions, and immediate follow-up reads across one block
- Exploit idea: serve one state layer from a stale cache while another layer commits a newer value
- Invariant to test: flat storage, trie reads, and committed state must agree on every accepted transition
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a same-block read-write-read test and assert every read layer returns the final committed value

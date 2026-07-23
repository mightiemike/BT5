# Q2252: deposit refund misrouting in global_contract::TryFrom

## Question
Can an unprivileged attacker submit a function-call transaction with attached deposit that reaches `core/primitives-core/src/global_contract.rs::TryFrom` with control over receiver id, callback tree, attached deposit, and failure ordering and make nearcore credit refunds according to one receipt path while execution consumes another, breaking the invariant that attached deposit and every refund leg must reconcile exactly across nested receipts, and leading to stealing or loss of funds?

## Target
- File/function: `core/primitives-core/src/global_contract.rs::TryFrom`
- Entrypoint: submit a function-call transaction with attached deposit
- Attacker controls: receiver id, callback tree, attached deposit, and failure ordering
- Exploit idea: credit refunds according to one receipt path while execution consumes another
- Invariant to test: attached deposit and every refund leg must reconcile exactly across nested receipts
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a nested-promise failure test and assert every refund lands at the intended account with the exact amount

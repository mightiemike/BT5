# Q3517: deposit refund misrouting in global_contracts::apply_distribution_current_shard

## Question
Can an unprivileged attacker submit a function-call transaction with attached deposit that reaches `runtime/runtime/src/global_contracts.rs::apply_distribution_current_shard` with control over receiver id, callback tree, attached deposit, and failure ordering and make nearcore credit refunds according to one receipt path while execution consumes another, breaking the invariant that attached deposit and every refund leg must reconcile exactly across nested receipts, and leading to stealing or loss of funds?

## Target
- File/function: `runtime/runtime/src/global_contracts.rs::apply_distribution_current_shard`
- Entrypoint: submit a function-call transaction with attached deposit
- Attacker controls: receiver id, callback tree, attached deposit, and failure ordering
- Exploit idea: credit refunds according to one receipt path while execution consumes another
- Invariant to test: attached deposit and every refund leg must reconcile exactly across nested receipts
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: write a nested-promise failure test and assert every refund lands at the intended account with the exact amount

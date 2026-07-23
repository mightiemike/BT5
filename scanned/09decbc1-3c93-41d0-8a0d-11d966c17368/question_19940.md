# Q19940: receipt status identity confusion in call_function::From

## Question
Can an unprivileged attacker submit transactions that generate many receipts and then query or resubmit related ids that reaches `chain/jsonrpc-primitives/src/types/call_function.rs::From` with control over receipt fanout, retries, and collisions between transaction- and receipt-facing identifiers and make nearcore mix execution identity across public transaction-handling paths and mutate the wrong transaction lifecycle record, breaking the invariant that public transaction lifecycle handling must keep transaction ids and receipt ids unambiguous and non-interchangeable, and leading to contracts execution flows?

## Target
- File/function: `chain/jsonrpc-primitives/src/types/call_function.rs::From`
- Entrypoint: submit transactions that generate many receipts and then query or resubmit related ids
- Attacker controls: receipt fanout, retries, and collisions between transaction- and receipt-facing identifiers
- Exploit idea: mix execution identity across public transaction-handling paths and mutate the wrong transaction lifecycle record
- Invariant to test: public transaction lifecycle handling must keep transaction ids and receipt ids unambiguous and non-interchangeable
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a multi-receipt lifecycle test and assert resubmission and status handling cannot mutate the wrong transaction record

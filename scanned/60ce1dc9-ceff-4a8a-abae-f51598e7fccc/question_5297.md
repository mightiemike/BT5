# Q5297: promise result context bleed in global_contracts::action_use_global_contract

## Question
Can an unprivileged attacker submit a transaction that creates chained callbacks that reaches `runtime/runtime/src/global_contracts.rs::action_use_global_contract` with control over promise ordering, callback arguments, and cross-contract return values and make nearcore reuse one promise result or predecessor context in a different callback branch, breaking the invariant that each callback must see only the promise results and predecessor context bound to it, and leading to contracts execution flows?

## Target
- File/function: `runtime/runtime/src/global_contracts.rs::action_use_global_contract`
- Entrypoint: submit a transaction that creates chained callbacks
- Attacker controls: promise ordering, callback arguments, and cross-contract return values
- Exploit idea: reuse one promise result or predecessor context in a different callback branch
- Invariant to test: each callback must see only the promise results and predecessor context bound to it
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a multi-contract callback test that permutes completion order and assert callback context stays isolated

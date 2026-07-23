# Q5165: promise result context bleed in ethabi_utils::try_from_token

## Question
Can an unprivileged attacker submit a transaction that creates chained callbacks that reaches `runtime/near-wallet-contract/implementation/wallet-contract/src/ethabi_utils.rs::try_from_token` with control over promise ordering, callback arguments, and cross-contract return values and make nearcore reuse one promise result or predecessor context in a different callback branch, breaking the invariant that each callback must see only the promise results and predecessor context bound to it, and leading to contracts execution flows?

## Target
- File/function: `runtime/near-wallet-contract/implementation/wallet-contract/src/ethabi_utils.rs::try_from_token`
- Entrypoint: submit a transaction that creates chained callbacks
- Attacker controls: promise ordering, callback arguments, and cross-contract return values
- Exploit idea: reuse one promise result or predecessor context in a different callback branch
- Invariant to test: each callback must see only the promise results and predecessor context bound to it
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a multi-contract callback test that permutes completion order and assert callback context stays isolated

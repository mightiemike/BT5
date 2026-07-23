# Q11466: receipt reauthorization confusion in transaction::nonce_index

## Question
Can an unprivileged attacker submit a transaction that spawns follow-up receipts that reaches `core/primitives/src/transaction.rs::nonce_index` with control over original signer context, receiver chain, and callback structure and make nearcore reuse signer or access-key authority when downstream receipts should execute only as protocol-generated work, breaking the invariant that receipt execution must never inherit more user authority than the original signed action allowed, and leading to contracts execution flows?

## Target
- File/function: `core/primitives/src/transaction.rs::nonce_index`
- Entrypoint: submit a transaction that spawns follow-up receipts
- Attacker controls: original signer context, receiver chain, and callback structure
- Exploit idea: reuse signer or access-key authority when downstream receipts should execute only as protocol-generated work
- Invariant to test: receipt execution must never inherit more user authority than the original signed action allowed
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a multi-receipt test that inspects downstream signer and predecessor context after callbacks and refunds

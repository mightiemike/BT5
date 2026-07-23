# Q11193: receipt reauthorization confusion in delegate::get_nep461_hash

## Question
Can an unprivileged attacker submit a transaction that spawns follow-up receipts that reaches `core/primitives/src/action/delegate.rs::get_nep461_hash` with control over original signer context, receiver chain, and callback structure and make nearcore reuse signer or access-key authority when downstream receipts should execute only as protocol-generated work, breaking the invariant that receipt execution must never inherit more user authority than the original signed action allowed, and leading to contracts execution flows?

## Target
- File/function: `core/primitives/src/action/delegate.rs::get_nep461_hash`
- Entrypoint: submit a transaction that spawns follow-up receipts
- Attacker controls: original signer context, receiver chain, and callback structure
- Exploit idea: reuse signer or access-key authority when downstream receipts should execute only as protocol-generated work
- Invariant to test: receipt execution must never inherit more user authority than the original signed action allowed
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a multi-receipt test that inspects downstream signer and predecessor context after callbacks and refunds

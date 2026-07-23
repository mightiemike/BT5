# Q6090: signature context drift in signable_message::off_chain_nep

## Question
Can an unprivileged attacker submit a signed transaction through default-enabled RPC or mempool admission that reaches `core/primitives/src/signable_message.rs::off_chain_nep` with control over serialized transaction bytes that preserve one signature but alter surrounding signed context and make nearcore accept a signature that is valid for one transaction image while executing another logical transaction, breaking the invariant that signature verification must bind every executed field of the transaction image, and leading to cryptographic flaws?

## Target
- File/function: `core/primitives/src/signable_message.rs::off_chain_nep`
- Entrypoint: submit a signed transaction through default-enabled RPC or mempool admission
- Attacker controls: serialized transaction bytes that preserve one signature but alter surrounding signed context
- Exploit idea: accept a signature that is valid for one transaction image while executing another logical transaction
- Invariant to test: signature verification must bind every executed field of the transaction image
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a serialization test that keeps the same signature while mutating a bound field and assert validation rejects the mutation

# Q5598: signature context drift in client::update_validator_signer

## Question
Can an unprivileged attacker submit a signed transaction through default-enabled RPC or mempool admission that reaches `chain/client/src/client.rs::update_validator_signer` with control over serialized transaction bytes that preserve one signature but alter surrounding signed context and make nearcore accept a signature that is valid for one transaction image while executing another logical transaction, breaking the invariant that signature verification must bind every executed field of the transaction image, and leading to cryptographic flaws?

## Target
- File/function: `chain/client/src/client.rs::update_validator_signer`
- Entrypoint: submit a signed transaction through default-enabled RPC or mempool admission
- Attacker controls: serialized transaction bytes that preserve one signature but alter surrounding signed context
- Exploit idea: accept a signature that is valid for one transaction image while executing another logical transaction
- Invariant to test: signature verification must bind every executed field of the transaction image
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a serialization test that keeps the same signature while mutating a bound field and assert validation rejects the mutation

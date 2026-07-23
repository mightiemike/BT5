# Q376: signature domain separation gap in hash::_hash_to_scalar

## Question
Can an unprivileged attacker submit a signed transaction or delegated payload that reaches `core/crypto/src/hash.rs::_hash_to_scalar` with control over a valid signature plus message fields that sit on domain or context boundaries and make nearcore accept one signature in a broader domain than the signer intended, breaking the invariant that every signature domain must bind message type, chain context, and execution meaning exactly, and leading to cryptographic flaws?

## Target
- File/function: `core/crypto/src/hash.rs::_hash_to_scalar`
- Entrypoint: submit a signed transaction or delegated payload
- Attacker controls: a valid signature plus message fields that sit on domain or context boundaries
- Exploit idea: accept one signature in a broader domain than the signer intended
- Invariant to test: every signature domain must bind message type, chain context, and execution meaning exactly
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a signing test that reuses one signature across adjacent message domains and assert cross-domain verification fails

# Q15717: cross-domain key acceptance in alt_bn128::encode_u256

## Question
Can an unprivileged attacker submit signed payloads that mix key types or curves accepted by normal user flows that reaches `runtime/near-vm-runner/src/logic/alt_bn128.rs::encode_u256` with control over keys and signatures from adjacent accepted formats and make nearcore verify a key or signature in the wrong cryptographic domain, breaking the invariant that accepted key formats must remain segregated by their intended verification domain, and leading to cryptographic flaws?

## Target
- File/function: `runtime/near-vm-runner/src/logic/alt_bn128.rs::encode_u256`
- Entrypoint: submit signed payloads that mix key types or curves accepted by normal user flows
- Attacker controls: keys and signatures from adjacent accepted formats
- Exploit idea: verify a key or signature in the wrong cryptographic domain
- Invariant to test: accepted key formats must remain segregated by their intended verification domain
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a mixed-key-format test and assert each verification path rejects keys from the wrong domain

# Q15961: cross-domain key acceptance in contract_code::hash

## Question
Can an unprivileged attacker submit signed payloads that mix key types or curves accepted by normal user flows that reaches `runtime/runtime/src/contract_code.rs::hash` with control over keys and signatures from adjacent accepted formats and make nearcore verify a key or signature in the wrong cryptographic domain, breaking the invariant that accepted key formats must remain segregated by their intended verification domain, and leading to cryptographic flaws?

## Target
- File/function: `runtime/runtime/src/contract_code.rs::hash`
- Entrypoint: submit signed payloads that mix key types or curves accepted by normal user flows
- Attacker controls: keys and signatures from adjacent accepted formats
- Exploit idea: verify a key or signature in the wrong cryptographic domain
- Invariant to test: accepted key formats must remain segregated by their intended verification domain
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a mixed-key-format test and assert each verification path rejects keys from the wrong domain

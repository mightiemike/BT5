# Q14676: cross-domain key acceptance in vrf::is_vrf_valid

## Question
Can an unprivileged attacker submit signed payloads that mix key types or curves accepted by normal user flows that reaches `core/crypto/src/vrf.rs::is_vrf_valid` with control over keys and signatures from adjacent accepted formats and make nearcore verify a key or signature in the wrong cryptographic domain, breaking the invariant that accepted key formats must remain segregated by their intended verification domain, and leading to cryptographic flaws?

## Target
- File/function: `core/crypto/src/vrf.rs::is_vrf_valid`
- Entrypoint: submit signed payloads that mix key types or curves accepted by normal user flows
- Attacker controls: keys and signatures from adjacent accepted formats
- Exploit idea: verify a key or signature in the wrong cryptographic domain
- Invariant to test: accepted key formats must remain segregated by their intended verification domain
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: write a mixed-key-format test and assert each verification path rejects keys from the wrong domain

# Q1613: deserialize 2026 from stream serde2026 parse magic prefix with malformed body via read cache lookup before and after pop

## Question
Can an unprivileged attacker reach `deserialize_2026_from_stream` in `src/serde_2026/de.rs` through public serde_2026 parsing or length analysis through `deserialize_2026_from_stream`, using a crafted magic prefix with malformed body input and the read cache lookup before and after pop validation path while controlling instruction streams referencing prior nodes, so the code computing length for a different decoded tree, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that auto detection must not weaken validation and causing High Python/Rust API divergence: auto and direct serde_2026 APIs disagree?

## Target
- File/function: src/serde_2026/de.rs::deserialize_2026_from_stream
- Entrypoint: public serde_2026 parsing or length analysis through `deserialize_2026_from_stream`
- Attacker controls: instruction streams referencing prior nodes
- Exploit idea: Build the smallest CLVM blob/program/API call for magic prefix with malformed body, drive it through read cache lookup before and after pop, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not weaken validation
- Expected Immunefi impact: High Python/Rust API divergence: auto and direct serde_2026 APIs disagree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

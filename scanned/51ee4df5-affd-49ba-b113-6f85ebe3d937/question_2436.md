# Q2436: read varint serde2026 parse strict=false versus strict=true acceptance via round trip through tree hash and bytes

## Question
Can an unprivileged attacker reach `read_varint` in `src/serde_2026/varint.rs` through public serde_2026 parsing or length analysis through `read_varint`, using a crafted strict=false versus strict=true acceptance input and the round trip through tree hash and bytes validation path while controlling strict mode and auto-detection inputs, so the code weakening direct parser validation through auto-detection, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that serde_2026 strict parsing must be canonical and causing Critical tree identity corruption: decoded tree is wrong?

## Target
- File/function: src/serde_2026/varint.rs::read_varint
- Entrypoint: public serde_2026 parsing or length analysis through `read_varint`
- Attacker controls: strict mode and auto-detection inputs
- Exploit idea: Build the smallest CLVM blob/program/API call for strict=false versus strict=true acceptance, drive it through round trip through tree hash and bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: serde_2026 strict parsing must be canonical
- Expected Immunefi impact: Critical tree identity corruption: decoded tree is wrong
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

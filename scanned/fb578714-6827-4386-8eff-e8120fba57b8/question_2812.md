# Q2812: emit instructions serde2026 ser left-first traversal boundary via legacy parser versus backref parser

## Question
Can an unprivileged attacker reach `emit_instructions` in `src/serde_2026/ser.rs` through public serde_2026 serialization through `emit_instructions`, using a crafted left-first traversal boundary input and the legacy parser versus backref parser validation path while controlling left/right visit strategy shape, so the code losing left/right pair order, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that level saturation must not change semantics and causing High Python/Rust API divergence: level handling changes decoded tree unexpectedly?

## Target
- File/function: src/serde_2026/ser.rs::emit_instructions
- Entrypoint: public serde_2026 serialization through `emit_instructions`
- Attacker controls: left/right visit strategy shape
- Exploit idea: Build the smallest CLVM blob/program/API call for left-first traversal boundary, drive it through legacy parser versus backref parser, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: level saturation must not change semantics
- Expected Immunefi impact: High Python/Rust API divergence: level handling changes decoded tree unexpectedly
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

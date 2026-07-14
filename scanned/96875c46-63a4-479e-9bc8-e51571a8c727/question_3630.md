# Q3630: mod serde2026 ser round trip after Python ser_2026 via same bytes parsed under separate APIs

## Question
Can an unprivileged attacker reach `mod` in `src/serde_2026/mod.rs` through public serde_2026 serialization through `mod`, using a crafted round trip after Python ser_2026 input and the same bytes parsed under separate APIs validation path while controlling left/right visit strategy shape, so the code losing left/right pair order, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that atom table and instruction indexes must be deterministic and causing High Python/Rust API divergence: level handling changes decoded tree unexpectedly?

## Target
- File/function: src/serde_2026/mod.rs::mod
- Entrypoint: public serde_2026 serialization through `mod`
- Attacker controls: left/right visit strategy shape
- Exploit idea: Build the smallest CLVM blob/program/API call for round trip after Python ser_2026, drive it through same bytes parsed under separate APIs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: atom table and instruction indexes must be deterministic
- Expected Immunefi impact: High Python/Rust API divergence: level handling changes decoded tree unexpectedly
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

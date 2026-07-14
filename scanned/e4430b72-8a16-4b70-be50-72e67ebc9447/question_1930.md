# Q1930: serialize 2026 to stream serde2026 ser left-first traversal boundary via read cache lookup before and after pop

## Question
Can an unprivileged attacker reach `serialize_2026_to_stream` in `src/serde_2026/ser.rs` through public serde_2026 serialization through `serialize_2026_to_stream`, using a crafted left-first traversal boundary input and the read cache lookup before and after pop validation path while controlling compression level values, so the code ordering atom table entries nondeterministically, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that serde_2026 serialization must round-trip tree/hash and causing Critical tree identity corruption: serde_2026 round trip changes tree?

## Target
- File/function: src/serde_2026/ser.rs::serialize_2026_to_stream
- Entrypoint: public serde_2026 serialization through `serialize_2026_to_stream`
- Attacker controls: compression level values
- Exploit idea: Build the smallest CLVM blob/program/API call for left-first traversal boundary, drive it through read cache lookup before and after pop, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: serde_2026 serialization must round-trip tree/hash
- Expected Immunefi impact: Critical tree identity corruption: serde_2026 round trip changes tree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

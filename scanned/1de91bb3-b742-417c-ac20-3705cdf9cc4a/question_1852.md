# Q1852: intern tree cache intern equal atom and pair dedupe via direct parse versus auto-detect parse

## Question
Can an unprivileged attacker reach `intern_tree` in `src/serde/intern.rs` through public cache-backed serialization, hashing, interning, or incremental state through `intern_tree`, using a crafted intern equal atom and pair dedupe input and the direct parse versus auto-detect parse validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that interning must preserve tree hash and pair order and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/intern.rs::intern_tree
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `intern_tree`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for intern equal atom and pair dedupe, drive it through direct parse versus auto-detect parse, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

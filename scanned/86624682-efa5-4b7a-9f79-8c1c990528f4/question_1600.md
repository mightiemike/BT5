# Q1600: intern tree limited cache intern equal atom and pair dedupe via legacy parser versus backref parser

## Question
Can an unprivileged attacker reach `intern_tree_limited` in `src/serde/intern.rs` through public cache-backed serialization, hashing, interning, or incremental state through `intern_tree_limited`, using a crafted intern equal atom and pair dedupe input and the legacy parser versus backref parser validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that interning must preserve tree hash and pair order and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/intern.rs::intern_tree_limited
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `intern_tree_limited`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for intern equal atom and pair dedupe, drive it through legacy parser versus backref parser, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3368: is empty cache TreeCache checkpoint restore via same tree allocated twice in distinct allocators

## Question
Can an unprivileged attacker reach `is_empty` in `src/serde/path_builder.rs` through public cache-backed serialization, hashing, interning, or incremental state through `is_empty`, using a crafted TreeCache checkpoint restore input and the same tree allocated twice in distinct allocators validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that interning must preserve tree hash and pair order and causing Critical tree identity corruption: stale cache returns wrong tree/hash?

## Target
- File/function: src/serde/path_builder.rs::is_empty
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `is_empty`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through same tree allocated twice in distinct allocators, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: Critical tree identity corruption: stale cache returns wrong tree/hash
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

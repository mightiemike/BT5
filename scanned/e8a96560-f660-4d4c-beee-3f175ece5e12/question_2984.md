# Q2984: finish cache TreeCache checkpoint restore via pre-eval callback enabled versus disabled

## Question
Can an unprivileged attacker reach `finish` in `src/serde/identity_hash.rs` through public cache-backed serialization, hashing, interning, or incremental state through `finish`, using a crafted TreeCache checkpoint restore input and the pre-eval callback enabled versus disabled validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that cached length/hash/path must match uncached computation and causing Critical tree identity corruption: stale cache returns wrong tree/hash?

## Target
- File/function: src/serde/identity_hash.rs::finish
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `finish`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through pre-eval callback enabled versus disabled, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cached length/hash/path must match uncached computation
- Expected Immunefi impact: Critical tree identity corruption: stale cache returns wrong tree/hash
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

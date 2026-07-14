# Q1988: undo state cache TreeCache checkpoint restore via same tree allocated twice in distinct allocators

## Question
Can an unprivileged attacker reach `undo_state` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `undo_state`, using a crafted TreeCache checkpoint restore input and the same tree allocated twice in distinct allocators validation path while controlling shared children across allocator checkpoints, so the code deduplicating nodes while changing pair order, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that cached length/hash/path must match uncached computation and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/tree_cache.rs::undo_state
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `undo_state`
- Attacker controls: shared children across allocator checkpoints
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through same tree allocated twice in distinct allocators, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cached length/hash/path must match uncached computation
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3878: push cache TreeCache checkpoint restore via cost limit at exact operator boundary

## Question
Can an unprivileged attacker reach `push` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `push`, using a crafted TreeCache checkpoint restore input and the cost limit at exact operator boundary validation path while controlling cache keys over similar but distinct trees, so the code using a cache key missing attacker-controlled tree data, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that interning must preserve tree hash and pair order and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/tree_cache.rs::push
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `push`
- Attacker controls: cache keys over similar but distinct trees
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through cost limit at exact operator boundary, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

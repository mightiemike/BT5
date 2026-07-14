# Q3626: add parent cache TreeCache checkpoint restore via tree cache checkpoint before and after restore

## Question
Can an unprivileged attacker reach `add_parent` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `add_parent`, using a crafted TreeCache checkpoint restore input and the tree cache checkpoint before and after restore validation path while controlling cache keys over similar but distinct trees, so the code using a cache key missing attacker-controlled tree data, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that interning must preserve tree hash and pair order and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/tree_cache.rs::add_parent
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `add_parent`
- Attacker controls: cache keys over similar but distinct trees
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through tree cache checkpoint before and after restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

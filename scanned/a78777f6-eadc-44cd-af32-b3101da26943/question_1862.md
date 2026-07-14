# Q1862: hash atom cache TreeCache checkpoint restore via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `hash_atom` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `hash_atom`, using a crafted TreeCache checkpoint restore input and the serde_2026 direct versus serde auto validation path while controlling cache keys over similar but distinct trees, so the code using a cache key missing attacker-controlled tree data, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that interning must preserve tree hash and pair order and causing Critical tree identity corruption: stale cache returns wrong tree/hash?

## Target
- File/function: src/serde/tree_cache.rs::hash_atom
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `hash_atom`
- Attacker controls: cache keys over similar but distinct trees
- Exploit idea: Build the smallest CLVM blob/program/API call for TreeCache checkpoint restore, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: Critical tree identity corruption: stale cache returns wrong tree/hash
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3114: calculate cache IdentityHash repeated key pattern via nil atom reused inside pair

## Question
Can an unprivileged attacker reach `calculate` in `src/serde/object_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `calculate`, using a crafted IdentityHash repeated key pattern input and the nil atom reused inside pair validation path while controlling cache keys over similar but distinct trees, so the code using a cache key missing attacker-controlled tree data, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that interning must preserve tree hash and pair order and causing Critical tree identity corruption: stale cache returns wrong tree/hash?

## Target
- File/function: src/serde/object_cache.rs::calculate
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `calculate`
- Attacker controls: cache keys over similar but distinct trees
- Exploit idea: Build the smallest CLVM blob/program/API call for IdentityHash repeated key pattern, drive it through nil atom reused inside pair, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: Critical tree identity corruption: stale cache returns wrong tree/hash
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

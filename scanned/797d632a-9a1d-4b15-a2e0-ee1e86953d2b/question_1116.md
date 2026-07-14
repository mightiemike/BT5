# Q1116: tree hash pair core tree hash exact atom bytes via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `tree_hash_pair` in `src/treehash.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `tree_hash_pair`, using a crafted tree hash exact atom bytes input and the serde_2026 direct versus serde auto validation path while controlling path atoms with leading zero and high-bit patterns, so the code computing hash or traversal over normalized instead of exact bytes, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that path traversal must match CLVM first/rest semantics and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/treehash.rs::tree_hash_pair
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `tree_hash_pair`
- Attacker controls: path atoms with leading zero and high-bit patterns
- Exploit idea: Build the smallest CLVM blob/program/API call for tree hash exact atom bytes, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: path traversal must match CLVM first/rest semantics
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

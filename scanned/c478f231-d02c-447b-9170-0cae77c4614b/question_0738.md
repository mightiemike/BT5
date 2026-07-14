# Q738: tree hash pair core tree hash exact atom bytes via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `tree_hash_pair` in `src/treehash.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `tree_hash_pair`, using a crafted tree hash exact atom bytes input and the fresh allocator versus checkpoint restore validation path while controlling nil, atom, pair, and checkpoint restore sequences, so the code keeping invalidated node state observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that valid NodePtr type/identity must remain stable and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/treehash.rs::tree_hash_pair
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `tree_hash_pair`
- Attacker controls: nil, atom, pair, and checkpoint restore sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for tree hash exact atom bytes, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

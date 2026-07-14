# Q1966: i32 from u8 core first/rest high-bit traversal via default flags versus MEMPOOL_MODE

## Question
Can an unprivileged attacker reach `i32_from_u8` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `i32_from_u8`, using a crafted first/rest high-bit traversal input and the default flags versus MEMPOOL_MODE validation path while controlling nil, atom, pair, and checkpoint restore sequences, so the code keeping invalidated node state observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that valid NodePtr type/identity must remain stable and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/op_utils.rs::i32_from_u8
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `i32_from_u8`
- Attacker controls: nil, atom, pair, and checkpoint restore sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for first/rest high-bit traversal, drive it through default flags versus MEMPOOL_MODE, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q640: lib core first/rest high-bit traversal via direct parse versus auto-detect parse

## Question
Can an unprivileged attacker reach `lib` in `src/lib.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `lib`, using a crafted first/rest high-bit traversal input and the direct parse versus auto-detect parse validation path while controlling nil, atom, pair, and checkpoint restore sequences, so the code keeping invalidated node state observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/lib.rs::lib
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `lib`
- Attacker controls: nil, atom, pair, and checkpoint restore sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for first/rest high-bit traversal, drive it through direct parse versus auto-detect parse, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

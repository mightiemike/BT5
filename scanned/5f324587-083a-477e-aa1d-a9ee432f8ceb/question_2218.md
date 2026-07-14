# Q2218: nilp core first/rest high-bit traversal via pair path all-left versus all-right

## Question
Can an unprivileged attacker reach `nilp` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `nilp`, using a crafted first/rest high-bit traversal input and the pair path all-left versus all-right validation path while controlling nil, atom, pair, and checkpoint restore sequences, so the code keeping invalidated node state observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that valid NodePtr type/identity must remain stable and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/op_utils.rs::nilp
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `nilp`
- Attacker controls: nil, atom, pair, and checkpoint restore sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for first/rest high-bit traversal, drive it through pair path all-left versus all-right, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

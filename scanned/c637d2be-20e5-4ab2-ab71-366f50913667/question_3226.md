# Q3226: int atom core first/rest high-bit traversal via nil atom reused inside pair

## Question
Can an unprivileged attacker reach `int_atom` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `int_atom`, using a crafted first/rest high-bit traversal input and the nil atom reused inside pair validation path while controlling nil, atom, pair, and checkpoint restore sequences, so the code keeping invalidated node state observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that valid NodePtr type/identity must remain stable and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/op_utils.rs::int_atom
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `int_atom`
- Attacker controls: nil, atom, pair, and checkpoint restore sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for first/rest high-bit traversal, drive it through nil atom reused inside pair, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

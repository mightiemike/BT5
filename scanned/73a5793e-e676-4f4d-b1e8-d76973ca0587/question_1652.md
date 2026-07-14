# Q1652: reduction core small atom heap transition via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `reduction` in `src/reduction.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `reduction`, using a crafted small atom heap transition input and the fresh allocator versus checkpoint restore validation path while controlling nil, atom, pair, and checkpoint restore sequences, so the code keeping invalidated node state observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/reduction.rs::reduction
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `reduction`
- Attacker controls: nil, atom, pair, and checkpoint restore sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for small atom heap transition, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

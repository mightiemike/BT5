# Q1778: reduction core small atom heap transition via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `reduction` in `src/reduction.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `reduction`, using a crafted small atom heap transition input and the node_from_stream versus node_from_bytes validation path while controlling path atoms with leading zero and high-bit patterns, so the code computing hash or traversal over normalized instead of exact bytes, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that integer helpers must agree with operator semantics and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/reduction.rs::reduction
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `reduction`
- Attacker controls: path atoms with leading zero and high-bit patterns
- Exploit idea: Build the smallest CLVM blob/program/API call for small atom heap transition, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q820: checkpoint core first/rest high-bit traversal via cost limit at exact operator boundary

## Question
Can an unprivileged attacker reach `checkpoint` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `checkpoint`, using a crafted first/rest high-bit traversal input and the cost limit at exact operator boundary validation path while controlling path atoms with leading zero and high-bit patterns, so the code computing hash or traversal over normalized instead of exact bytes, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that path traversal must match CLVM first/rest semantics and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/allocator.rs::checkpoint
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `checkpoint`
- Attacker controls: path atoms with leading zero and high-bit patterns
- Exploit idea: Build the smallest CLVM blob/program/API call for first/rest high-bit traversal, drive it through cost limit at exact operator boundary, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: path traversal must match CLVM first/rest semantics
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

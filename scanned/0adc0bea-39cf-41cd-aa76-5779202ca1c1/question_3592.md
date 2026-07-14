# Q3592: new pair core first/rest high-bit traversal via same tree allocated twice in distinct allocators

## Question
Can an unprivileged attacker reach `new_pair` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `new_pair`, using a crafted first/rest high-bit traversal input and the same tree allocated twice in distinct allocators validation path while controlling path atoms with leading zero and high-bit patterns, so the code computing hash or traversal over normalized instead of exact bytes, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that path traversal must match CLVM first/rest semantics and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/allocator.rs::new_pair
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `new_pair`
- Attacker controls: path atoms with leading zero and high-bit patterns
- Exploit idea: Build the smallest CLVM blob/program/API call for first/rest high-bit traversal, drive it through same tree allocated twice in distinct allocators, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: path traversal must match CLVM first/rest semantics
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

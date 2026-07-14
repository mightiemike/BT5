# Q328: i32 from u8 core first/rest high-bit traversal via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `i32_from_u8` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `i32_from_u8`, using a crafted first/rest high-bit traversal input and the fresh allocator versus checkpoint restore validation path while controlling path atoms with leading zero and high-bit patterns, so the code computing hash or traversal over normalized instead of exact bytes, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that path traversal must match CLVM first/rest semantics and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/op_utils.rs::i32_from_u8
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `i32_from_u8`
- Attacker controls: path atoms with leading zero and high-bit patterns
- Exploit idea: Build the smallest CLVM blob/program/API call for first/rest high-bit traversal, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: path traversal must match CLVM first/rest semantics
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

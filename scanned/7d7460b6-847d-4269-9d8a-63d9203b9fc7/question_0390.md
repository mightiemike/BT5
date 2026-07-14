# Q390: bits core tree hash exact atom bytes via writer limit at exact output length

## Question
Can an unprivileged attacker reach `bits` in `src/number.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `bits`, using a crafted tree hash exact atom bytes input and the writer limit at exact output length validation path while controlling path atoms with leading zero and high-bit patterns, so the code computing hash or traversal over normalized instead of exact bytes, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that tree hash must use exact atom bytes and pair order and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/number.rs::bits
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `bits`
- Attacker controls: path atoms with leading zero and high-bit patterns
- Exploit idea: Build the smallest CLVM blob/program/API call for tree hash exact atom bytes, drive it through writer limit at exact output length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

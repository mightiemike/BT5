# Q379: deref core empty atom versus nil boundary via direct parse versus auto-detect parse

## Question
Can an unprivileged attacker reach `deref` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `deref`, using a crafted empty atom versus nil boundary input and the direct parse versus auto-detect parse validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/allocator.rs::deref
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `deref`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through direct parse versus auto-detect parse, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

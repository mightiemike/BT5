# Q2647: mk node core empty atom versus nil boundary via pair path all-left versus all-right

## Question
Can an unprivileged attacker reach `mk_node` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `mk_node`, using a crafted empty atom versus nil boundary input and the pair path all-left versus all-right validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/allocator.rs::mk_node
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `mk_node`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through pair path all-left versus all-right, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

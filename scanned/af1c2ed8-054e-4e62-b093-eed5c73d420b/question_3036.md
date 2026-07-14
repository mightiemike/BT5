# Q3036: bits core tree hash exact atom bytes via legacy parser versus backref parser

## Question
Can an unprivileged attacker reach `bits` in `src/number.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `bits`, using a crafted tree hash exact atom bytes input and the legacy parser versus backref parser validation path while controlling nil, atom, pair, and checkpoint restore sequences, so the code keeping invalidated node state observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that integer helpers must agree with operator semantics and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/number.rs::bits
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `bits`
- Attacker controls: nil, atom, pair, and checkpoint restore sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for tree hash exact atom bytes, drive it through legacy parser versus backref parser, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

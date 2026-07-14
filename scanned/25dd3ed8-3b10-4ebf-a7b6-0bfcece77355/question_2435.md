# Q2435: cons opcode serde2026 ser large atom table index via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `cons_opcode` in `src/serde_2026/strategy.rs` through public serde_2026 serialization through `cons_opcode`, using a crafted large atom table index input and the fresh allocator versus checkpoint restore validation path while controlling atom ordering and reference counts, so the code changing semantics when level exceeds implemented range, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that level saturation must not change semantics and causing Critical tree identity corruption: serde_2026 round trip changes tree?

## Target
- File/function: src/serde_2026/strategy.rs::cons_opcode
- Entrypoint: public serde_2026 serialization through `cons_opcode`
- Attacker controls: atom ordering and reference counts
- Exploit idea: Build the smallest CLVM blob/program/API call for large atom table index, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: level saturation must not change semantics
- Expected Immunefi impact: Critical tree identity corruption: serde_2026 round trip changes tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

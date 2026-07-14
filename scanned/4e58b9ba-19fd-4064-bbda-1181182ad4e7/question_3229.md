# Q3229: allow unknown ops execution cost one unit below required work via execute then serialize backrefs

## Question
Can an unprivileged attacker reach `allow_unknown_ops` in `src/runtime_dialect.rs` through public CLVM execution through `allow_unknown_ops` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the execute then serialize backrefs validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that same program/env/flags/max_cost must produce identical result, error, and cost and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: src/runtime_dialect.rs::allow_unknown_ops
- Entrypoint: public CLVM execution through `allow_unknown_ops` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through execute then serialize backrefs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: same program/env/flags/max_cost must produce identical result, error, and cost
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

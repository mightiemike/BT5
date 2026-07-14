# Q2591: opcode by name execution softfork guard followed by disabled opcode via pair path all-left versus all-right

## Question
Can an unprivileged attacker reach `opcode_by_name` in `src/f_table.rs` through public CLVM execution through `opcode_by_name` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted softfork guard followed by disabled opcode input and the pair path all-left versus all-right validation path while controlling quote/apply/softfork program atoms, so the code returning result/error/cost different from an equivalent supported path, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that operator availability must follow active dialect and softfork state and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: src/f_table.rs::opcode_by_name
- Entrypoint: public CLVM execution through `opcode_by_name` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: quote/apply/softfork program atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for softfork guard followed by disabled opcode, drive it through pair path all-left versus all-right, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3669: run program execution cost one unit below required work via tree_hash before and after intern_tree

## Question
Can an unprivileged attacker reach `run_program` in `src/run_program.rs` through public CLVM execution through `run_program` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the tree_hash before and after intern_tree validation path while controlling quote/apply/softfork program atoms, so the code returning result/error/cost different from an equivalent supported path, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that cost and limit checks must precede consensus-visible output and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: src/run_program.rs::run_program
- Entrypoint: public CLVM execution through `run_program` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: quote/apply/softfork program atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through tree_hash before and after intern_tree, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost and limit checks must precede consensus-visible output
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1757: curry binding LazyNode pair then atom access via Program.run_with_cost versus run_serialized_chia_program

## Question
Can an unprivileged attacker reach `curry` in `wheel/python/clvm_rs/curry_and_treehash.py` through public Python/Rust binding API `curry` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the Program.run_with_cost versus run_serialized_chia_program validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that Python conversion must snapshot one stable tree and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/curry_and_treehash.py::curry
- Entrypoint: public Python/Rust binding API `curry` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through Program.run_with_cost versus run_serialized_chia_program, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

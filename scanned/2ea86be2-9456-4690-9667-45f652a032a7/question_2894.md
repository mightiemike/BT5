# Q2894: int to bytes binding memoryview versus bytes cast via stream hash versus tree hash

## Question
Can an unprivileged attacker reach `int_to_bytes` in `wheel/python/clvm_rs/program.py` through public Python/Rust binding API `int_to_bytes` with attacker-controlled Python or byte inputs, using a crafted memoryview versus bytes cast input and the stream hash versus tree hash validation path while controlling Python max_cost and flags values, so the code mapping Rust error into misleading Python state, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that Python conversion must snapshot one stable tree and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/program.py::int_to_bytes
- Entrypoint: public Python/Rust binding API `int_to_bytes` with attacker-controlled Python or byte inputs
- Attacker controls: Python max_cost and flags values
- Exploit idea: Build the smallest CLVM blob/program/API call for memoryview versus bytes cast, drive it through stream hash versus tree hash, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

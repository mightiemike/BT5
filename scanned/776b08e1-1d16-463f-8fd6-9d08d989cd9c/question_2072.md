# Q2072: curry and treehash binding memoryview versus bytes cast via allocator debug semantics versus release semantics

## Question
Can an unprivileged attacker reach `curry_and_treehash` in `wheel/python/clvm_rs/curry_and_treehash.py` through public Python/Rust binding API `curry_and_treehash` with attacker-controlled Python or byte inputs, using a crafted memoryview versus bytes cast input and the allocator debug semantics versus release semantics validation path while controlling Python max_cost and flags values, so the code mapping Rust error into misleading Python state, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that auto detection must not accept bytes direct parser rejects and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/curry_and_treehash.py::curry_and_treehash
- Entrypoint: public Python/Rust binding API `curry_and_treehash` with attacker-controlled Python or byte inputs
- Attacker controls: Python max_cost and flags values
- Exploit idea: Build the smallest CLVM blob/program/API call for memoryview versus bytes cast, drive it through allocator debug semantics versus release semantics, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

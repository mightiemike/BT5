# Q3832: to clvm object binding Python max_cost truncation boundary via read cache lookup before and after pop

## Question
Can an unprivileged attacker reach `to_clvm_object` in `wheel/python/clvm_rs/casts.py` through public Python/Rust binding API `to_clvm_object` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the read cache lookup before and after pop validation path while controlling Python max_cost and flags values, so the code mapping Rust error into misleading Python state, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that auto detection must not accept bytes direct parser rejects and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/casts.py::to_clvm_object
- Entrypoint: public Python/Rust binding API `to_clvm_object` with attacker-controlled Python or byte inputs
- Attacker controls: Python max_cost and flags values
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through read cache lookup before and after pop, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

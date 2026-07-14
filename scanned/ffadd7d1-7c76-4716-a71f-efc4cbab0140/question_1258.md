# Q1258: sexp from stream binding Python max_cost truncation boundary via fast path versus generic path

## Question
Can an unprivileged attacker reach `sexp_from_stream` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the fast path versus generic path validation path while controlling Python max_cost and flags values, so the code mapping Rust error into misleading Python state, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that LazyNode must expose exact allocator-backed result and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/ser.py::sexp_from_stream
- Entrypoint: public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs
- Attacker controls: Python max_cost and flags values
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through fast path versus generic path, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

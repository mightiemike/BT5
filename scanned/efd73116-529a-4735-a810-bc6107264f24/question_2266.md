# Q2266: op read sexp binding Python max_cost truncation boundary via full serialization versus cached serialization

## Question
Can an unprivileged attacker reach `_op_read_sexp` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `_op_read_sexp` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the full serialization versus cached serialization validation path while controlling Python max_cost and flags values, so the code mapping Rust error into misleading Python state, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that LazyNode must expose exact allocator-backed result and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/ser.py::_op_read_sexp
- Entrypoint: public Python/Rust binding API `_op_read_sexp` with attacker-controlled Python or byte inputs
- Attacker controls: Python max_cost and flags values
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through full serialization versus cached serialization, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

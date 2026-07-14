# Q3964: str binding Python max_cost truncation boundary via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `__str__` in `wheel/python/clvm_rs/eval_error.py` through public Python/Rust binding API `__str__` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the fresh allocator versus checkpoint restore validation path while controlling Python max_cost and flags values, so the code mapping Rust error into misleading Python state, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that Python and Rust APIs must agree on result/cost/error/bytes/hash and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/eval_error.py::__str__
- Entrypoint: public Python/Rust binding API `__str__` with attacker-controlled Python or byte inputs
- Attacker controls: Python max_cost and flags values
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python and Rust APIs must agree on result/cost/error/bytes/hash
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

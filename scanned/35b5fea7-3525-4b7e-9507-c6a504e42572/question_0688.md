# Q688: init binding Python max_cost truncation boundary via writer limit at exact output length

## Question
Can an unprivileged attacker reach `__init__` in `wheel/python/clvm_rs/eval_error.py` through public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the writer limit at exact output length validation path while controlling Python max_cost and flags values, so the code mapping Rust error into misleading Python state, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that Python and Rust APIs must agree on result/cost/error/bytes/hash and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/eval_error.py::__init__
- Entrypoint: public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs
- Attacker controls: Python max_cost and flags values
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through writer limit at exact output length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python and Rust APIs must agree on result/cost/error/bytes/hash
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1825: sexp from stream binding mutable Python object during conversion via maximum small atom then heap atom

## Question
Can an unprivileged attacker reach `sexp_from_stream` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the maximum small atom then heap atom validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that Python and Rust APIs must agree on result/cost/error/bytes/hash and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/ser.py::sexp_from_stream
- Entrypoint: public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through maximum small atom then heap atom, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python and Rust APIs must agree on result/cost/error/bytes/hash
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

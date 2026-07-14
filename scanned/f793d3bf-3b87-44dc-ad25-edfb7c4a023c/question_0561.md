# Q561: atom size from cursor binding format auto magic-prefix boundary via execute then serialize legacy

## Question
Can an unprivileged attacker reach `_atom_size_from_cursor` in `wheel/python/clvm_rs/de.py` through public Python/Rust binding API `_atom_size_from_cursor` with attacker-controlled Python or byte inputs, using a crafted format auto magic-prefix boundary input and the execute then serialize legacy validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that Python and Rust APIs must agree on result/cost/error/bytes/hash and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/de.py::_atom_size_from_cursor
- Entrypoint: public Python/Rust binding API `_atom_size_from_cursor` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for format auto magic-prefix boundary, drive it through execute then serialize legacy, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python and Rust APIs must agree on result/cost/error/bytes/hash
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

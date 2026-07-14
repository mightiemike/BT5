# Q124: sexp from stream binding Python max_cost truncation boundary via same bytes parsed under separate APIs

## Question
Can an unprivileged attacker reach `sexp_from_stream` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the same bytes parsed under separate APIs validation path while controlling Python CLVMStorage atom/pair properties, so the code converting a Python object into a different tree than serialized or hashed, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that Python conversion must snapshot one stable tree and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/ser.py::sexp_from_stream
- Entrypoint: public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs
- Attacker controls: Python CLVMStorage atom/pair properties
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through same bytes parsed under separate APIs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

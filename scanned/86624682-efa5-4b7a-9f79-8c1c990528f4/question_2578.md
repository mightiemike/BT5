# Q2578: init binding Python max_cost truncation boundary via Python API versus Rust API

## Question
Can an unprivileged attacker reach `__init__` in `wheel/python/clvm_rs/eval_error.py` through public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the Python API versus Rust API validation path while controlling Python CLVMStorage atom/pair properties, so the code converting a Python object into a different tree than serialized or hashed, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that auto detection must not accept bytes direct parser rejects and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/eval_error.py::__init__
- Entrypoint: public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs
- Attacker controls: Python CLVMStorage atom/pair properties
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through Python API versus Rust API, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

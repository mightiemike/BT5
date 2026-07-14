# Q1946: init binding memoryview versus bytes cast via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `__init__` in `wheel/python/clvm_rs/curry_and_treehash.py` through public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs, using a crafted memoryview versus bytes cast input and the strict mode versus non-strict mode where exposed validation path while controlling Python CLVMStorage atom/pair properties, so the code converting a Python object into a different tree than serialized or hashed, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that Python and Rust APIs must agree on result/cost/error/bytes/hash and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/curry_and_treehash.py::__init__
- Entrypoint: public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs
- Attacker controls: Python CLVMStorage atom/pair properties
- Exploit idea: Build the smallest CLVM blob/program/API call for memoryview versus bytes cast, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python and Rust APIs must agree on result/cost/error/bytes/hash
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

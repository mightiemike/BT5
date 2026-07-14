# Q3272: new pair binding memoryview versus bytes cast via fast path versus generic path

## Question
Can an unprivileged attacker reach `new_pair` in `wheel/python/clvm_rs/program.py` through public Python/Rust binding API `new_pair` with attacker-controlled Python or byte inputs, using a crafted memoryview versus bytes cast input and the fast path versus generic path validation path while controlling Python CLVMStorage atom/pair properties, so the code converting a Python object into a different tree than serialized or hashed, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that LazyNode must expose exact allocator-backed result and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/program.py::new_pair
- Entrypoint: public Python/Rust binding API `new_pair` with attacker-controlled Python or byte inputs
- Attacker controls: Python CLVMStorage atom/pair properties
- Exploit idea: Build the smallest CLVM blob/program/API call for memoryview versus bytes cast, drive it through fast path versus generic path, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

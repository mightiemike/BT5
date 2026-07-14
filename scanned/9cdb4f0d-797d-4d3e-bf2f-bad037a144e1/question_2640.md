# Q2640: save index binding Program bytes/tree_hash/run comparison via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `save_index` in `wheel/python/clvm_rs/de.py` through public Python/Rust binding API `save_index` with attacker-controlled Python or byte inputs, using a crafted Program bytes/tree_hash/run comparison input and the fresh allocator versus checkpoint restore validation path while controlling Python CLVMStorage atom/pair properties, so the code converting a Python object into a different tree than serialized or hashed, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that Python conversion must snapshot one stable tree and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/de.py::save_index
- Entrypoint: public Python/Rust binding API `save_index` with attacker-controlled Python or byte inputs
- Attacker controls: Python CLVMStorage atom/pair properties
- Exploit idea: Build the smallest CLVM blob/program/API call for Program bytes/tree_hash/run comparison, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3400: op read sexp binding Python max_cost truncation boundary via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `_op_read_sexp` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `_op_read_sexp` with attacker-controlled Python or byte inputs, using a crafted Python max_cost truncation boundary input and the node_from_stream versus node_from_bytes validation path while controlling Python CLVMStorage atom/pair properties, so the code converting a Python object into a different tree than serialized or hashed, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that Python conversion must snapshot one stable tree and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/ser.py::_op_read_sexp
- Entrypoint: public Python/Rust binding API `_op_read_sexp` with attacker-controlled Python or byte inputs
- Attacker controls: Python CLVMStorage atom/pair properties
- Exploit idea: Build the smallest CLVM blob/program/API call for Python max_cost truncation boundary, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1127: curried values tree hash binding LazyNode pair then atom access via tree cache checkpoint before and after restore

## Question
Can an unprivileged attacker reach `curried_values_tree_hash` in `wheel/python/clvm_rs/curry_and_treehash.py` through public Python/Rust binding API `curried_values_tree_hash` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the tree cache checkpoint before and after restore validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that LazyNode must expose exact allocator-backed result and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/curry_and_treehash.py::curried_values_tree_hash
- Entrypoint: public Python/Rust binding API `curried_values_tree_hash` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through tree cache checkpoint before and after restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

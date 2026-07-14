# Q2387: init binding LazyNode pair then atom access via pre-eval callback enabled versus disabled

## Question
Can an unprivileged attacker reach `__init__` in `wheel/python/clvm_rs/curry_and_treehash.py` through public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the pre-eval callback enabled versus disabled validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that LazyNode must expose exact allocator-backed result and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/curry_and_treehash.py::__init__
- Entrypoint: public Python/Rust binding API `__init__` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through pre-eval callback enabled versus disabled, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

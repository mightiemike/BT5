# Q3395: curry and treehash binding LazyNode pair then atom access via counters mode versus normal mode

## Question
Can an unprivileged attacker reach `curry_and_treehash` in `wheel/python/clvm_rs/curry_and_treehash.py` through public Python/Rust binding API `curry_and_treehash` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the counters mode versus normal mode validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that LazyNode must expose exact allocator-backed result and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/curry_and_treehash.py::curry_and_treehash
- Entrypoint: public Python/Rust binding API `curry_and_treehash` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through counters mode versus normal mode, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

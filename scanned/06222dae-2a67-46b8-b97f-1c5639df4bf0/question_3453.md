# Q3453: at binding format auto magic-prefix boundary via Python Program wrapper versus low-level LazyNode

## Question
Can an unprivileged attacker reach `at` in `wheel/python/clvm_rs/at.py` through public Python/Rust binding API `at` with attacker-controlled Python or byte inputs, using a crafted format auto magic-prefix boundary input and the Python Program wrapper versus low-level LazyNode validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that auto detection must not accept bytes direct parser rejects and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/at.py::at
- Entrypoint: public Python/Rust binding API `at` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for format auto magic-prefix boundary, drive it through Python Program wrapper versus low-level LazyNode, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

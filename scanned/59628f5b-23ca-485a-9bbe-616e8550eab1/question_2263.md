# Q2263: str binding mutable Python object during conversion via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `__str__` in `wheel/python/clvm_rs/eval_error.py` through public Python/Rust binding API `__str__` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the strict mode versus non-strict mode where exposed validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that Python conversion must snapshot one stable tree and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/eval_error.py::__str__
- Entrypoint: public Python/Rust binding API `__str__` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

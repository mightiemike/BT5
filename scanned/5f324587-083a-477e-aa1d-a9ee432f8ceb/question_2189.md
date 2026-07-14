# Q2189: deser legacy binding LazyNode pair then atom access via serialized_length_from_bytes versus trusted length

## Question
Can an unprivileged attacker reach `deser_legacy` in `wheel/src/api.rs` through public Python/Rust binding API `deser_legacy` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the serialized_length_from_bytes versus trusted length validation path while controlling bytes, memoryview, and integer casting boundaries, so the code returning Python-visible result, error, cost, bytes, or hash different from Rust core, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that auto detection must not accept bytes direct parser rejects and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/src/api.rs::deser_legacy
- Entrypoint: public Python/Rust binding API `deser_legacy` with attacker-controlled Python or byte inputs
- Attacker controls: bytes, memoryview, and integer casting boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through serialized_length_from_bytes versus trusted length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

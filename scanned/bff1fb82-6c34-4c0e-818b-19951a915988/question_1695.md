# Q1695: save index binding format auto magic-prefix boundary via writer limit at exact output length

## Question
Can an unprivileged attacker reach `save_index` in `wheel/python/clvm_rs/de.py` through public Python/Rust binding API `save_index` with attacker-controlled Python or byte inputs, using a crafted format auto magic-prefix boundary input and the writer limit at exact output length validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that auto detection must not accept bytes direct parser rejects and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/de.py::save_index
- Entrypoint: public Python/Rust binding API `save_index` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for format auto magic-prefix boundary, drive it through writer limit at exact output length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

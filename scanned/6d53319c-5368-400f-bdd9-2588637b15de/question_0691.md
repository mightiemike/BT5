# Q691: sexp from stream binding mutable Python object during conversion via counters mode versus normal mode

## Question
Can an unprivileged attacker reach `sexp_from_stream` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the counters mode versus normal mode validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that auto detection must not accept bytes direct parser rejects and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/ser.py::sexp_from_stream
- Entrypoint: public Python/Rust binding API `sexp_from_stream` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through counters mode versus normal mode, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

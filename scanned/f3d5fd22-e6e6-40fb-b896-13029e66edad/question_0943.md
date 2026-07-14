# Q943: atom to byte iterator binding mutable Python object during conversion via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `atom_to_byte_iterator` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `atom_to_byte_iterator` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the fresh allocator versus checkpoint restore validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that auto detection must not accept bytes direct parser rejects and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/ser.py::atom_to_byte_iterator
- Entrypoint: public Python/Rust binding API `atom_to_byte_iterator` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

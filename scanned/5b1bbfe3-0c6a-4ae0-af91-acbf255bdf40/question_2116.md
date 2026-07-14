# Q2116: write atom serializer nil versus empty atom serialization via fast path versus generic path

## Question
Can an unprivileged attacker reach `write_atom` in `src/serde/write_atom.rs` through public serialization through `write_atom` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted nil versus empty atom serialization input and the fast path versus generic path validation path while controlling trees with repeated atoms, so the code serializing bytes that deserialize to a different tree, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that atom prefixes must be canonical for exact length and causing Critical canonical serialization failure: emitted bytes decode ambiguously?

## Target
- File/function: src/serde/write_atom.rs::write_atom
- Entrypoint: public serialization through `write_atom` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: trees with repeated atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for nil versus empty atom serialization, drive it through fast path versus generic path, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: atom prefixes must be canonical for exact length
- Expected Immunefi impact: Critical canonical serialization failure: emitted bytes decode ambiguously
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

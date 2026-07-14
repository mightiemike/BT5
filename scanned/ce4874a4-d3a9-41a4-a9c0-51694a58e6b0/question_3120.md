# Q3120: serialized length atom serializer legacy round trip after Python Program.stream via direct parse versus auto-detect parse

## Question
Can an unprivileged attacker reach `serialized_length_atom` in `src/serde/serialized_length.rs` through public serialization through `serialized_length_atom` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted legacy round trip after Python Program.stream input and the direct parse versus auto-detect parse validation path while controlling trees with repeated atoms, so the code serializing bytes that deserialize to a different tree, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that atom prefixes must be canonical for exact length and causing Critical canonical serialization failure: emitted bytes decode ambiguously?

## Target
- File/function: src/serde/serialized_length.rs::serialized_length_atom
- Entrypoint: public serialization through `serialized_length_atom` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: trees with repeated atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for legacy round trip after Python Program.stream, drive it through direct parse versus auto-detect parse, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: atom prefixes must be canonical for exact length
- Expected Immunefi impact: Critical canonical serialization failure: emitted bytes decode ambiguously
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

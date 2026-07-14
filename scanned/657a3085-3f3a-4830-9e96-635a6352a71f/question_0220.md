# Q220: node to stream serializer nil versus empty atom serialization via legacy parser versus backref parser

## Question
Can an unprivileged attacker reach `node_to_stream` in `src/serde/ser.rs` through public serialization through `node_to_stream` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted nil versus empty atom serialization input and the legacy parser versus backref parser validation path while controlling trees with repeated atoms, so the code serializing bytes that deserialize to a different tree, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that pair order and nil/atom distinction must be preserved and causing Critical canonical serialization failure: emitted bytes decode ambiguously?

## Target
- File/function: src/serde/ser.rs::node_to_stream
- Entrypoint: public serialization through `node_to_stream` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: trees with repeated atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for nil versus empty atom serialization, drive it through legacy parser versus backref parser, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: pair order and nil/atom distinction must be preserved
- Expected Immunefi impact: Critical canonical serialization failure: emitted bytes decode ambiguously
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

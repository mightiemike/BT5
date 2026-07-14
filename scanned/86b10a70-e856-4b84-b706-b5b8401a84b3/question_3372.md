# Q3372: serialized length small number serializer legacy round trip after Python Program.stream via mempool mode followed by block mode replay

## Question
Can an unprivileged attacker reach `serialized_length_small_number` in `src/serde/serialized_length.rs` through public serialization through `serialized_length_small_number` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted legacy round trip after Python Program.stream input and the mempool mode followed by block mode replay validation path while controlling trees with repeated atoms, so the code serializing bytes that deserialize to a different tree, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that atom prefixes must be canonical for exact length and causing Critical tree identity corruption: serialization changes tree/hash?

## Target
- File/function: src/serde/serialized_length.rs::serialized_length_small_number
- Entrypoint: public serialization through `serialized_length_small_number` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: trees with repeated atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for legacy round trip after Python Program.stream, drive it through mempool mode followed by block mode replay, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: atom prefixes must be canonical for exact length
- Expected Immunefi impact: Critical tree identity corruption: serialization changes tree/hash
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

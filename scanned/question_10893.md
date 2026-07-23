# Q10893: receipt ordering nondeterminism in logic::make_partial_encoded_chunk_from_owned_parts_and_needed_receipts

## Question
Can an unprivileged attacker submit transactions whose callbacks and refunds interleave across shards that reaches `chain/chunks/src/logic.rs::make_partial_encoded_chunk_from_owned_parts_and_needed_receipts` with control over contract logic that emits multiple receipts with attacker-chosen ordering pressure and make nearcore let runtime-visible ordering depend on a noncanonical iteration or merge order, breaking the invariant that the same accepted transaction and receipt set must produce one deterministic execution order and state root, and leading to consensus flaws?

## Target
- File/function: `chain/chunks/src/logic.rs::make_partial_encoded_chunk_from_owned_parts_and_needed_receipts`
- Entrypoint: submit transactions whose callbacks and refunds interleave across shards
- Attacker controls: contract logic that emits multiple receipts with attacker-chosen ordering pressure
- Exploit idea: let runtime-visible ordering depend on a noncanonical iteration or merge order
- Invariant to test: the same accepted transaction and receipt set must produce one deterministic execution order and state root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a deterministic multi-node or property test that replays the same receipt set under different internal ordering and assert identical final roots

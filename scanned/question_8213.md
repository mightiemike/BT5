# Q8213: noncanonical root derivation in merkle_proof::compute_past_block_proof_in_merkle_tree_of_later_block

## Question
Can an unprivileged attacker submit transactions that create many attacker-controlled writes to the same shard that reaches `core/store/src/merkle_proof.rs::compute_past_block_proof_in_merkle_tree_of_later_block` with control over key order, receipt order, and transaction grouping within one accepted block and make nearcore compute final state from a noncanonical iteration or merge order, breaking the invariant that the same accepted write set must always yield one canonical state root, and leading to consensus flaws?

## Target
- File/function: `core/store/src/merkle_proof.rs::compute_past_block_proof_in_merkle_tree_of_later_block`
- Entrypoint: submit transactions that create many attacker-controlled writes to the same shard
- Attacker controls: key order, receipt order, and transaction grouping within one accepted block
- Exploit idea: compute final state from a noncanonical iteration or merge order
- Invariant to test: the same accepted write set must always yield one canonical state root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a deterministic root test that permutes equivalent write orders and assert the final root stays identical

# Q8301: noncanonical root derivation in construction::encode_leftmost_to_vec

## Question
Can an unprivileged attacker submit transactions that create many attacker-controlled writes to the same shard that reaches `core/store/src/trie/mem/construction.rs::encode_leftmost_to_vec` with control over key order, receipt order, and transaction grouping within one accepted block and make nearcore compute final state from a noncanonical iteration or merge order, breaking the invariant that the same accepted write set must always yield one canonical state root, and leading to consensus flaws?

## Target
- File/function: `core/store/src/trie/mem/construction.rs::encode_leftmost_to_vec`
- Entrypoint: submit transactions that create many attacker-controlled writes to the same shard
- Attacker controls: key order, receipt order, and transaction grouping within one accepted block
- Exploit idea: compute final state from a noncanonical iteration or merge order
- Invariant to test: the same accepted write set must always yield one canonical state root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a deterministic root test that permutes equivalent write orders and assert the final root stays identical

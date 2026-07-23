# Q7245: noncanonical root derivation in trie_state_resharder::start_resharding_blocking

## Question
Can an unprivileged attacker submit transactions that create many attacker-controlled writes to the same shard that reaches `chain/chain/src/resharding/trie_state_resharder.rs::start_resharding_blocking` with control over key order, receipt order, and transaction grouping within one accepted block and make nearcore compute final state from a noncanonical iteration or merge order, breaking the invariant that the same accepted write set must always yield one canonical state root, and leading to consensus flaws?

## Target
- File/function: `chain/chain/src/resharding/trie_state_resharder.rs::start_resharding_blocking`
- Entrypoint: submit transactions that create many attacker-controlled writes to the same shard
- Attacker controls: key order, receipt order, and transaction grouping within one accepted block
- Exploit idea: compute final state from a noncanonical iteration or merge order
- Invariant to test: the same accepted write set must always yield one canonical state root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a deterministic root test that permutes equivalent write orders and assert the final root stays identical

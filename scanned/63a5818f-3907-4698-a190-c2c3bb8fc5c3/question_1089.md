# Q1089: trie-key aliasing in merkle_proof::compute_past_block_proof_in_merkle_tree_of_later_block

## Question
Can an unprivileged attacker submit transactions or contract calls that create overlapping account and storage keys that reaches `core/store/src/merkle_proof.rs::compute_past_block_proof_in_merkle_tree_of_later_block` with control over account ids, storage keys, and write-delete order chosen to stress encoding boundaries and make nearcore map two logically distinct values onto one storage location or lookup path, breaking the invariant that distinct logical state items must always map to distinct trie paths and persisted values, and leading to balance manipulation?

## Target
- File/function: `core/store/src/merkle_proof.rs::compute_past_block_proof_in_merkle_tree_of_later_block`
- Entrypoint: submit transactions or contract calls that create overlapping account and storage keys
- Attacker controls: account ids, storage keys, and write-delete order chosen to stress encoding boundaries
- Exploit idea: map two logically distinct values onto one storage location or lookup path
- Invariant to test: distinct logical state items must always map to distinct trie paths and persisted values
- Expected Immunefi impact: Balance manipulation
- Fast validation: write a trie-level test that creates adversarial key pairs and assert lookups and deletes stay disjoint

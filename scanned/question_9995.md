# Q9995: split or merge misrouting in merkle_proof::get_block_hash_from_ordinal

## Question
Can an unprivileged attacker submit contract calls that create state and receipts near shard mapping boundaries that reaches `core/store/src/merkle_proof.rs::get_block_hash_from_ordinal` with control over account ids and storage keys chosen to sit on shard-layout edge cases and make nearcore send a value or receipt to the wrong child shard during split, merge, or remap logic, breaking the invariant that shard remapping must preserve the exact destination shard for every key and receipt, and leading to contracts execution flows?

## Target
- File/function: `core/store/src/merkle_proof.rs::get_block_hash_from_ordinal`
- Entrypoint: submit contract calls that create state and receipts near shard mapping boundaries
- Attacker controls: account ids and storage keys chosen to sit on shard-layout edge cases
- Exploit idea: send a value or receipt to the wrong child shard during split, merge, or remap logic
- Invariant to test: shard remapping must preserve the exact destination shard for every key and receipt
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a shard-layout edge-case test that checks every moved key and receipt lands in the expected shard

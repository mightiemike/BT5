# Q3748: cross-shard routing confusion in chunk_cache::mark_decode_failed

## Question
Can an unprivileged attacker submit a transaction that creates cross-shard receipts that reaches `chain/chunks/src/chunk_cache.rs::mark_decode_failed` with control over receiver ids, shard-placement edge cases, and callback trees and make nearcore route execution to a different shard or height than the one implied by the canonical mapping, breaking the invariant that every cross-shard receipt must execute on the one canonical destination shard and height, and leading to contracts execution flows?

## Target
- File/function: `chain/chunks/src/chunk_cache.rs::mark_decode_failed`
- Entrypoint: submit a transaction that creates cross-shard receipts
- Attacker controls: receiver ids, shard-placement edge cases, and callback trees
- Exploit idea: route execution to a different shard or height than the one implied by the canonical mapping
- Invariant to test: every cross-shard receipt must execute on the one canonical destination shard and height
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a cross-shard routing test on edge-case account ids and assert receipt destinations remain canonical

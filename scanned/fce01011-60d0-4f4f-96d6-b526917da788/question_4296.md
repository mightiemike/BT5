# Q4296: cross-shard routing confusion in shard_chunk_header_inner::height_created

## Question
Can an unprivileged attacker submit a transaction that creates cross-shard receipts that reaches `core/primitives/src/sharding/shard_chunk_header_inner.rs::height_created` with control over receiver ids, shard-placement edge cases, and callback trees and make nearcore route execution to a different shard or height than the one implied by the canonical mapping, breaking the invariant that every cross-shard receipt must execute on the one canonical destination shard and height, and leading to contracts execution flows?

## Target
- File/function: `core/primitives/src/sharding/shard_chunk_header_inner.rs::height_created`
- Entrypoint: submit a transaction that creates cross-shard receipts
- Attacker controls: receiver ids, shard-placement edge cases, and callback trees
- Exploit idea: route execution to a different shard or height than the one implied by the canonical mapping
- Invariant to test: every cross-shard receipt must execute on the one canonical destination shard and height
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a cross-shard routing test on edge-case account ids and assert receipt destinations remain canonical

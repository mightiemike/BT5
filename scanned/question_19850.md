# Q19850: user-driven canonical hash skew in client_actor::random_seed_from_thread

## Question
Can an unprivileged attacker submit transactions whose resulting receipts and state updates are logically equivalent but structurally diverse that reaches `chain/client/src/client_actor.rs::random_seed_from_thread` with control over transaction grouping and contract-generated receipt structure and make nearcore derive canonical identifiers or hashes from representation details that honest nodes need not preserve identically, breaking the invariant that canonical hashes and identifiers must depend only on the canonical executed content, not incidental representation choices, and leading to consensus flaws?

## Target
- File/function: `chain/client/src/client_actor.rs::random_seed_from_thread`
- Entrypoint: submit transactions whose resulting receipts and state updates are logically equivalent but structurally diverse
- Attacker controls: transaction grouping and contract-generated receipt structure
- Exploit idea: derive canonical identifiers or hashes from representation details that honest nodes need not preserve identically
- Invariant to test: canonical hashes and identifiers must depend only on the canonical executed content, not incidental representation choices
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a representation-variance test and assert canonical hashes remain identical for logically equivalent execution

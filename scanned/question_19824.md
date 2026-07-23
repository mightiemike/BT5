# Q19824: cross-contract callback authority reuse in chunk_inclusion_tracker::prepare_chunk_headers_ready_for_inclusion

## Question
Can an unprivileged attacker submit a transaction that chains callbacks across attacker-controlled and victim contracts that reaches `chain/client/src/chunk_inclusion_tracker.rs::prepare_chunk_headers_ready_for_inclusion` with control over callback targets, returned promises, and predecessor relationships and make nearcore let a callback reuse authority or funds from a prior leg after its intended scope has ended, breaking the invariant that callbacks must consume only the authority, deposit, and promise context explicitly passed into them, and leading to unauthorized transaction?

## Target
- File/function: `chain/client/src/chunk_inclusion_tracker.rs::prepare_chunk_headers_ready_for_inclusion`
- Entrypoint: submit a transaction that chains callbacks across attacker-controlled and victim contracts
- Attacker controls: callback targets, returned promises, and predecessor relationships
- Exploit idea: let a callback reuse authority or funds from a prior leg after its intended scope has ended
- Invariant to test: callbacks must consume only the authority, deposit, and promise context explicitly passed into them
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a chained-callback test that inspects predecessor, signer, and attached deposit across every leg

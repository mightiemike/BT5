# Q1407: Proof API oldest-retained plus tip-race

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block whose canonical hash was cached offchain from `get_last_block_header` one transaction earlier using prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification
- Invariant to test: economically relevant historical proofs must not become spuriously valid or invalid because of a race between tip growth and public pruning
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

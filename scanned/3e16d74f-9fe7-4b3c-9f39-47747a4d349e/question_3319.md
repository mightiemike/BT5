# Q3319: verify_transaction_inclusion oldest-retained plus tip-race false deposit acceptance

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose canonical hash was cached offchain from `get_last_block_header` one transaction earlier using prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification, so that verification returns `true` for a transaction that never funded the bridge and a downstream mint or unlock executes anyway?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification to force false deposit acceptance
- Invariant to test: economically relevant historical proofs must not become spuriously valid or invalid because of a race between tip growth and public pruning
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables false deposit acceptance.

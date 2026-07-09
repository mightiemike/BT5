# Q2838: verify_transaction_inclusion_v2 oldest-retained plus tip-race wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against the first block after a fork point that later becomes canonical using prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification to force wrong-block withdrawal unlock
- Invariant to test: economically relevant historical proofs must not become spuriously valid or invalid because of a race between tip growth and public pruning
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

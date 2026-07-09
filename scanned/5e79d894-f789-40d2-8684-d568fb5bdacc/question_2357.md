# Q2357: verify_transaction_inclusion_v2 oldest-retained plus tip-race double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block near the tip while the relayer is submitting the next batch using prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: prepare a proof at the oldest retained canonical block while the tip moves forward and a public caller prunes the tail just before verification to force double redemption across reorg
- Invariant to test: economically relevant historical proofs must not become spuriously valid or invalid because of a race between tip growth and public pruning
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

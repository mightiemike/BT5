# Q2630: verify_transaction_inclusion_v2 dual-canonical replay across short reorg wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block selected from `get_last_n_blocks_hashes` right before public GC trims the tail using target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary to force wrong-block withdrawal unlock
- Invariant to test: the light client must not let the same economic event be proven once on the old tip and again on the new tip as two independent confirmations
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

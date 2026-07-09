# Q2533: verify_transaction_inclusion_v2 dual-canonical replay across short reorg double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block that sits exactly `gc_threshold` confirmations behind the tip using target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary to force double redemption across reorg
- Invariant to test: the light client must not let the same economic event be proven once on the old tip and again on the new tip as two independent confirmations
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

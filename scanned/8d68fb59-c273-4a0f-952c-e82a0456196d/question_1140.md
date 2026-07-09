# Q1140: Proof API dual-canonical replay across short reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block hash obtained from `get_block_hash_by_height` just before a reorg changes that height mapping using target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary
- Invariant to test: the light client must not let the same economic event be proven once on the old tip and again on the new tip as two independent confirmations
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

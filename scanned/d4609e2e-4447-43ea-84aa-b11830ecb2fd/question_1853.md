# Q1853: verify_transaction_inclusion_v2 cached height-hash mismatch double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against the oldest retained canonical block immediately after `run_mainchain_gc` advances `mainchain_initial_blockhash` using reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain`, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain` to force double redemption across reorg
- Invariant to test: a proof prepared from a height lookup must fail cleanly once that height points to a different canonical block
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

# Q3105: verify_transaction_inclusion cached height-hash mismatch double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block observed by a downstream bridge before the light client finishes a reorg using reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain`, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain` to force double redemption across reorg
- Invariant to test: a proof prepared from a height lookup must fail cleanly once that height points to a different canonical block
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

# Q2201: verify_transaction_inclusion reorg-time proof replay double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block hash obtained from `get_block_hash_by_height` just before a reorg changes that height mapping using submit the same proof immediately before and immediately after the canonical height mapping changes during a reorg, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: submit the same proof immediately before and immediately after the canonical height mapping changes during a reorg to force double redemption across reorg
- Invariant to test: a proof that was valid for the displaced branch must not remain valid after the branch loses canonical status
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

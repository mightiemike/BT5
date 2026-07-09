# Q2885: verify_transaction_inclusion_v2 confirmation-boundary race double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a proof prepared against a block at the current `mainchain_initial_blockhash` using time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition to force double redemption across reorg
- Invariant to test: requested confirmations must attach to the same canonical block hash throughout verification, not just the same height
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

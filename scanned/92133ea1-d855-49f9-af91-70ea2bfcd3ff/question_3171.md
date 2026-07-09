# Q3171: verify_transaction_inclusion_v2 confirmation-boundary race false deposit acceptance

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block at the moment relayer recovery is walking backward through `get_last_n_blocks_hashes` using time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition, so that verification returns `true` for a transaction that never funded the bridge and a downstream mint or unlock executes anyway?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition to force false deposit acceptance
- Invariant to test: requested confirmations must attach to the same canonical block hash throughout verification, not just the same height
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables false deposit acceptance.

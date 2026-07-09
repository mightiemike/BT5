# Q1930: verify_transaction_inclusion confirmation-boundary race wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose height slot was recently rewritten by `reorg_chain` using time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition to force wrong-block withdrawal unlock
- Invariant to test: requested confirmations must attach to the same canonical block hash throughout verification, not just the same height
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

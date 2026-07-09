# Q2504: verify_transaction_inclusion confirmation-boundary race old-fork replay

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block that sits exactly `gc_threshold` confirmations behind the tip using time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition, so that verification returns `true` for a transaction that only belonged to the displaced canonical fork and the downstream system treats it as still settled?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition to force old-fork replay
- Invariant to test: requested confirmations must attach to the same canonical block hash throughout verification, not just the same height
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables old-fork replay.

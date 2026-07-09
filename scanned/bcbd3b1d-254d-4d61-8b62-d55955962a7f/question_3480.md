# Q3480: verify_transaction_inclusion gc-edge confirmation anchor old-fork replay

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose proof depth is the same as the coinbase proof depth but whose effective position changes after a reorg using anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary, so that verification returns `true` for a transaction that only belonged to the displaced canonical fork and the downstream system treats it as still settled?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary to force old-fork replay
- Invariant to test: proof validity must not change simply because a third party advances the GC boundary during economically relevant confirmation windows
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables old-fork replay.

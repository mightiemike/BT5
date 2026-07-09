# Q2420: verify_transaction_inclusion_v2 gc-edge confirmation anchor old-fork replay

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block that still exists in `headers_pool` but no longer in `mainchain_header_to_height` after fork cleanup using anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary, so that verification returns `true` for a transaction that only belonged to the displaced canonical fork and the downstream system treats it as still settled?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary to force old-fork replay
- Invariant to test: proof validity must not change simply because a third party advances the GC boundary during economically relevant confirmation windows
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables old-fork replay.

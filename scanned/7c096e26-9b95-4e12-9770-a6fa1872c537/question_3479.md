# Q3479: verify_transaction_inclusion gc-edge confirmation anchor false deposit acceptance

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose proof depth is the same as the coinbase proof depth but whose effective position changes after a reorg using anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary, so that verification returns `true` for a transaction that never funded the bridge and a downstream mint or unlock executes anyway?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary to force false deposit acceptance
- Invariant to test: proof validity must not change simply because a third party advances the GC boundary during economically relevant confirmation windows
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables false deposit acceptance.

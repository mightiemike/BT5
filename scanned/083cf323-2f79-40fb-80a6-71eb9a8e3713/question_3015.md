# Q3015: verify_transaction_inclusion dual-canonical replay across short reorg false deposit acceptance

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose coinbase path shares repeated sibling hashes because of duplicated odd leaves using target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary, so that verification returns `true` for a transaction that never funded the bridge and a downstream mint or unlock executes anyway?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: target a transaction that exists on both the old and new branches and see whether the same economic event can be proven twice across a short reorg boundary to force false deposit acceptance
- Invariant to test: the light client must not let the same economic event be proven once on the old tip and again on the new tip as two independent confirmations
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables false deposit acceptance.

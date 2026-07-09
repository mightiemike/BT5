# Q1787: verify_transaction_inclusion_v2 odd-leaf duplicate position reuse false deposit acceptance

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against the oldest retained canonical block immediately after `run_mainchain_gc` advances `mainchain_initial_blockhash` using reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree, so that verification returns `true` for a transaction that never funded the bridge and a downstream mint or unlock executes anyway?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree to force false deposit acceptance
- Invariant to test: a Merkle proof must bind to one exact transaction position, even when the tree duplicates its last leaf
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables false deposit acceptance.

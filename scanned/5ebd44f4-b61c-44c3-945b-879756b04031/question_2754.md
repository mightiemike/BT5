# Q2754: verify_transaction_inclusion odd-leaf duplicate position reuse wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against the first block after a fork point that later becomes canonical using reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree to force wrong-block withdrawal unlock
- Invariant to test: a Merkle proof must bind to one exact transaction position, even when the tree duplicates its last leaf
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

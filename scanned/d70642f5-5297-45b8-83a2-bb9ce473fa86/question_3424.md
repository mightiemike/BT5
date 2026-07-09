# Q3424: verify_transaction_inclusion odd-leaf duplicate position reuse old-fork replay

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose proof depth is the same as the coinbase proof depth but whose effective position changes after a reorg using reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree, so that verification returns `true` for a transaction that only belonged to the displaced canonical fork and the downstream system treats it as still settled?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree to force old-fork replay
- Invariant to test: a Merkle proof must bind to one exact transaction position, even when the tree duplicates its last leaf
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables old-fork replay.

# Q2144: verify_transaction_inclusion cached height-hash mismatch old-fork replay

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against an odd-width transaction tree where the last leaf is duplicated at one or more levels using reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain`, so that verification returns `true` for a transaction that only belonged to the displaced canonical fork and the downstream system treats it as still settled?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain` to force old-fork replay
- Invariant to test: a proof prepared from a height lookup must fail cleanly once that height points to a different canonical block
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables old-fork replay.

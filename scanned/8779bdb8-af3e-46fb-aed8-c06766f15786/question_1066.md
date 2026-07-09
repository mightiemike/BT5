# Q1066: Proof API cached height-hash mismatch

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose height slot was recently rewritten by `reorg_chain` using reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain`, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse a cached result from `get_block_hash_by_height` after that height is rewritten by `reorg_chain`
- Invariant to test: a proof prepared from a height lookup must fail cleanly once that height points to a different canonical block
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

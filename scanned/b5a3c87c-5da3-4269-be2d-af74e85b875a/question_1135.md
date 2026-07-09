# Q1135: Proof API gc-edge confirmation anchor

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block hash obtained from `get_block_hash_by_height` just before a reorg changes that height mapping using anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary
- Invariant to test: proof validity must not change simply because a third party advances the GC boundary during economically relevant confirmation windows
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

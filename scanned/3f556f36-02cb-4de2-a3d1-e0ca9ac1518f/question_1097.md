# Q1097: Proof API odd-leaf duplicate position reuse

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against an odd-width transaction tree where the last leaf is duplicated at one or more levels using reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: reuse the same duplicated-last-leaf branch for two distinct `tx_index` values in an odd-width tree
- Invariant to test: a Merkle proof must bind to one exact transaction position, even when the tree duplicates its last leaf
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

# Q1412: Proof API position-halving boundary

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block that moved from fork-only storage to canonical storage during the same relayer cycle using choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction
- Invariant to test: proof verification must consume the position bits in exactly the same order as the source chain's Merkle tree
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

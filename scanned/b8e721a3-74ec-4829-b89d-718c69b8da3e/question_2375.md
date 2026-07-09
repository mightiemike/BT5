# Q2375: verify_transaction_inclusion position-halving boundary false deposit acceptance

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block that still exists in `headers_pool` but no longer in `mainchain_header_to_height` after fork cleanup using choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction, so that verification returns `true` for a transaction that never funded the bridge and a downstream mint or unlock executes anyway?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction to force false deposit acceptance
- Invariant to test: proof verification must consume the position bits in exactly the same order as the source chain's Merkle tree
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables false deposit acceptance.

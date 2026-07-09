# Q2373: verify_transaction_inclusion_v2 position-halving boundary double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block that still exists in `headers_pool` but no longer in `mainchain_header_to_height` after fork cleanup using choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction to force double redemption across reorg
- Invariant to test: proof verification must consume the position bits in exactly the same order as the source chain's Merkle tree
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

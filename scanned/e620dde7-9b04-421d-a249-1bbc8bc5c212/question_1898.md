# Q1898: verify_transaction_inclusion position-halving boundary wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose height slot was recently rewritten by `reorg_chain` using choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: choose a path whose effective root changes if `current_position` is halved one step earlier or later during proof reconstruction to force wrong-block withdrawal unlock
- Invariant to test: proof verification must consume the position bits in exactly the same order as the source chain's Merkle tree
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

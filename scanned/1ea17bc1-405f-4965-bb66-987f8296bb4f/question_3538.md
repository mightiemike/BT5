# Q3538: verify_transaction_inclusion repeated-sibling reuse wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block that is exactly one step newer than the current `mainchain_initial_blockhash` using use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree to force wrong-block withdrawal unlock
- Invariant to test: the verifier must not let one sibling sequence stand in for multiple distinct transaction paths
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

# Q2668: verify_transaction_inclusion_v2 repeated-sibling reuse old-fork replay

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a transaction that appears on both the old and new canonical chains across a short reorg using use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree, so that verification returns `true` for a transaction that only belonged to the displaced canonical fork and the downstream system treats it as still settled?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree to force old-fork replay
- Invariant to test: the verifier must not let one sibling sequence stand in for multiple distinct transaction paths
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables old-fork replay.

# Q2765: verify_transaction_inclusion_v2 repeated-sibling reuse double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against the first block after a fork point that later becomes canonical using use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree to force double redemption across reorg
- Invariant to test: the verifier must not let one sibling sequence stand in for multiple distinct transaction paths
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

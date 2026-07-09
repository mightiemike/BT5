# Q2289: verify_transaction_inclusion repeated-sibling reuse double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block near the tip while the relayer is submitting the next batch using use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree to force double redemption across reorg
- Invariant to test: the verifier must not let one sibling sequence stand in for multiple distinct transaction paths
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.

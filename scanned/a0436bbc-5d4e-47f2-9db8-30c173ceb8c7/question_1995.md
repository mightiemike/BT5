# Q1995: verify_transaction_inclusion_v2 repeated-sibling reuse false deposit acceptance

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block that used to be canonical before a heavier fork replaced it using use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree, so that verification returns `true` for a transaction that never funded the bridge and a downstream mint or unlock executes anyway?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: use repeated equal sibling hashes so the same `merkle_proof` can plausibly validate more than one path inside the same odd-width tree to force false deposit acceptance
- Invariant to test: the verifier must not let one sibling sequence stand in for multiple distinct transaction paths
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables false deposit acceptance.

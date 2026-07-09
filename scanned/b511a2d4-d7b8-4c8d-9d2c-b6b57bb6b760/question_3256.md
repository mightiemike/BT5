# Q3256: verify_transaction_inclusion reorg-time proof replay old-fork replay

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose canonical hash was cached offchain from `get_last_block_header` one transaction earlier using submit the same proof immediately before and immediately after the canonical height mapping changes during a reorg, so that verification returns `true` for a transaction that only belonged to the displaced canonical fork and the downstream system treats it as still settled?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: submit the same proof immediately before and immediately after the canonical height mapping changes during a reorg to force old-fork replay
- Invariant to test: a proof that was valid for the displaced branch must not remain valid after the branch loses canonical status
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables old-fork replay.

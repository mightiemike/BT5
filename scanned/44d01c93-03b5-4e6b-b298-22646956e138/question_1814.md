# Q1814: verify_transaction_inclusion_v2 reorg-time proof replay wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against the oldest retained canonical block immediately after `run_mainchain_gc` advances `mainchain_initial_blockhash` using submit the same proof immediately before and immediately after the canonical height mapping changes during a reorg, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: submit the same proof immediately before and immediately after the canonical height mapping changes during a reorg to force wrong-block withdrawal unlock
- Invariant to test: a proof that was valid for the displaced branch must not remain valid after the branch loses canonical status
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

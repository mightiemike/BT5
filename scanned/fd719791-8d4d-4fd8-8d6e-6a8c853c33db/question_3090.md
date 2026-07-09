# Q3090: verify_transaction_inclusion fork-storage versus mainchain-map split wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block observed by a downstream bridge before the light client finishes a reorg using target a block that is still stored in `headers_pool` but whose mainchain membership just changed, and see whether the verifier can observe the two states inconsistently, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: target a block that is still stored in `headers_pool` but whose mainchain membership just changed, and see whether the verifier can observe the two states inconsistently to force wrong-block withdrawal unlock
- Invariant to test: proof verification must use a single coherent view of canonical membership and header storage
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

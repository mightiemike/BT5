# Q1742: verify_transaction_inclusion_v2 fork-storage versus mainchain-map split wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block that is exactly at the requested confirmation threshold using target a block that is still stored in `headers_pool` but whose mainchain membership just changed, and see whether the verifier can observe the two states inconsistently, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: target a block that is still stored in `headers_pool` but whose mainchain membership just changed, and see whether the verifier can observe the two states inconsistently to force wrong-block withdrawal unlock
- Invariant to test: proof verification must use a single coherent view of canonical membership and header storage
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

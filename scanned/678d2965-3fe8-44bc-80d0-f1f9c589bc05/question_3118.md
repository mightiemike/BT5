# Q3118: verify_transaction_inclusion_v2 proof-depth equality edge wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block observed by a downstream bridge before the light client finishes a reorg using choose a transaction path whose depth matches the coinbase proof depth exactly while its effective position changes when the branch reorganizes, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: choose a transaction path whose depth matches the coinbase proof depth exactly while its effective position changes when the branch reorganizes to force wrong-block withdrawal unlock
- Invariant to test: matching proof depth with the coinbase witness must not be enough to keep a stale transaction proof valid across canonical changes
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.

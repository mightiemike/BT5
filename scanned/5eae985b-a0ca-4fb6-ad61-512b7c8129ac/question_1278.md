# Q1278: Proof API fork-storage versus mainchain-map split

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against the first block after a fork point that later becomes canonical using target a block that is still stored in `headers_pool` but whose mainchain membership just changed, and see whether the verifier can observe the two states inconsistently, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: target a block that is still stored in `headers_pool` but whose mainchain membership just changed, and see whether the verifier can observe the two states inconsistently
- Invariant to test: proof verification must use a single coherent view of canonical membership and header storage
- Expected Immunefi impact: Cross-chain replay attack enabling double-spending
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

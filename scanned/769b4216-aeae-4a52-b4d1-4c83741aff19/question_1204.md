# Q1204: Proof API confirmation-boundary race

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block that sits exactly `gc_threshold` confirmations behind the tip using time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: time the proof so height arithmetic still satisfies the requested confirmation count while the underlying block hash is changing around a fork transition
- Invariant to test: requested confirmations must attach to the same canonical block hash throughout verification, not just the same height
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

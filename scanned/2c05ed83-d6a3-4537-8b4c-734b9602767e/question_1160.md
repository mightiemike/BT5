# Q1160: Proof API gc-edge confirmation anchor

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block near the tip while the relayer is submitting the next batch using anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary, so that a downstream bridge, unlock, mint, or withdrawal flow accepts a transaction that is nonexistent, no longer canonical, or economically replayed?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: anchor the proof to the exact oldest retained block while a public caller triggers GC around the same historical boundary
- Invariant to test: proof validity must not change simply because a third party advances the GC boundary during economically relevant confirmation windows
- Expected Immunefi impact: Stealing or loss of funds
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this state transition and assert it never returns `true` for a nonexistent, stale, or replayed economic event.

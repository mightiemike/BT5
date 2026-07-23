# Q8807: chunk total mismatch in simulator::get_previous_non_missing_chunk_height

## Question
Can an unprivileged attacker submit transactions that force many receipts, refunds, or gas-consuming callbacks into one chunk that reaches `runtime/runtime/src/bandwidth_scheduler/simulator.rs::get_previous_non_missing_chunk_height` with control over gas usage, deposits, and callback structure across a block-sized workload and make nearcore let header or chunk-level totals diverge from the state changes actually executed, breaking the invariant that chunk and block totals must exactly reconcile to the executed receipts, gas, and balance deltas, and leading to consensus flaws?

## Target
- File/function: `runtime/runtime/src/bandwidth_scheduler/simulator.rs::get_previous_non_missing_chunk_height`
- Entrypoint: submit transactions that force many receipts, refunds, or gas-consuming callbacks into one chunk
- Attacker controls: gas usage, deposits, and callback structure across a block-sized workload
- Exploit idea: let header or chunk-level totals diverge from the state changes actually executed
- Invariant to test: chunk and block totals must exactly reconcile to the executed receipts, gas, and balance deltas
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a chunk-heavy execution test and assert header totals reconcile with executed receipts and balances

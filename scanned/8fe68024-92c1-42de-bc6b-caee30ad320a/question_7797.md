# Q7797: chunk total mismatch in receipt::metadata

## Question
Can an unprivileged attacker submit transactions that force many receipts, refunds, or gas-consuming callbacks into one chunk that reaches `core/primitives/src/receipt.rs::metadata` with control over gas usage, deposits, and callback structure across a block-sized workload and make nearcore let header or chunk-level totals diverge from the state changes actually executed, breaking the invariant that chunk and block totals must exactly reconcile to the executed receipts, gas, and balance deltas, and leading to consensus flaws?

## Target
- File/function: `core/primitives/src/receipt.rs::metadata`
- Entrypoint: submit transactions that force many receipts, refunds, or gas-consuming callbacks into one chunk
- Attacker controls: gas usage, deposits, and callback structure across a block-sized workload
- Exploit idea: let header or chunk-level totals diverge from the state changes actually executed
- Invariant to test: chunk and block totals must exactly reconcile to the executed receipts, gas, and balance deltas
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a chunk-heavy execution test and assert header totals reconcile with executed receipts and balances

# Q300: Stale or double-applied withdrawPool

## Question
Can attacker-controlled sequencing make core/contracts/ClearinghouseStorage.sol / module-level logic consume stale withdrawPool or apply the same withdrawPool transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/ClearinghouseStorage.sol / module-level logic
- Entrypoint: User reaches ClearinghouseStorage-backed state through any deposit, withdrawal, liquidation, or settlement routed into Clearinghouse.
- Attacker controls: productId, engine routing, withdrawPool address assumptions, spreads encoding
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale withdrawPool before all related state is finalized.
- Invariant to test: Storage-backed engine routing, spread encoding, and insurance bookkeeping must not let user actions mutate the wrong product or pool.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation causing wrong engine updates, wrong withdrawal routing, or insolvent bookkeeping.
- Fast validation: Build a state-machine test that mutates product registration, spread-encoded products, and withdrawal routing assumptions through reachable user flows and assert storage-backed routing remains correct.

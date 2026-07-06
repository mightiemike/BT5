# Q6: Cross-contract desync of engineByType

## Question
Can a normal user drive core/contracts/ClearinghouseStorage.sol / module-level logic so that engineByType is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/ClearinghouseStorage.sol / module-level logic
- Entrypoint: User reaches ClearinghouseStorage-backed state through any deposit, withdrawal, liquidation, or settlement routed into Clearinghouse.
- Attacker controls: productId, engine routing, withdrawPool address assumptions, spreads encoding
- Exploit idea: Target the exact moment when core/contracts/ClearinghouseStorage.sol / module-level logic mutates engineByType and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Storage-backed engine routing, spread encoding, and insurance bookkeeping must not let user actions mutate the wrong product or pool.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation causing wrong engine updates, wrong withdrawal routing, or insolvent bookkeeping.
- Fast validation: Build a state-machine test that mutates product registration, spread-encoded products, and withdrawal routing assumptions through reachable user flows and assert storage-backed routing remains correct.

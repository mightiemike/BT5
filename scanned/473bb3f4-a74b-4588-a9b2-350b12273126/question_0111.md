# Q111: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/ClearinghouseStorage.sol / module-level logic behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/ClearinghouseStorage.sol / module-level logic
- Entrypoint: User reaches ClearinghouseStorage-backed state through any deposit, withdrawal, liquidation, or settlement routed into Clearinghouse.
- Attacker controls: productId, engine routing, withdrawPool address assumptions, spreads encoding
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/ClearinghouseStorage.sol / module-level logic, especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Storage-backed engine routing, spread encoding, and insurance bookkeeping must not let user actions mutate the wrong product or pool.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Build a state-machine test that mutates product registration, spread-encoded products, and withdrawal routing assumptions through reachable user flows and assert storage-backed routing remains correct.

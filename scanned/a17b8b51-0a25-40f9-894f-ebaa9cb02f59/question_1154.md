# Q1154: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Spot balances, borrow/deposit multipliers, and utilization checks must conserve value across deposits, withdrawals, fills, NLP, and liquidation.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Write invariants that compare spot balances, actual token custody, and utilization after every reachable deposit/withdraw/fill/NLP transition.

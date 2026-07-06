# Q1532: Failure-handling mismatch after perpEngine.settlePnl(...)

## Question
Can attacker-controlled failure behavior around perpEngine.settlePnl(...) leave core/contracts/Clearinghouse.sol / checkMinDeposit(uint32 productId, uint128 amount, int256 minDepositAmount) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/Clearinghouse.sol / checkMinDeposit(uint32 productId, uint128 amount, int256 minDepositAmount)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Force perpEngine.settlePnl(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: External asset transfers must not happen in a way that leaves user balances or protocol balances inconsistent after failure or reentrancy.
- Expected HackenProof impact: Critical/High: stealing or loss of funds from the exchange, withdraw pool, or insurance accounting.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.

# Q1550: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/Clearinghouse.sol / checkMinDeposit(uint32 productId, uint128 amount, int256 minDepositAmount) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/Clearinghouse.sol / checkMinDeposit(uint32 productId, uint128 amount, int256 minDepositAmount)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/Clearinghouse.sol / checkMinDeposit(uint32 productId, uint128 amount, int256 minDepositAmount), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.

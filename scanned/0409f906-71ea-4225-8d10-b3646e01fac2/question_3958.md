# Q3958: Reentrancy or stale-state window at BaseWithdrawPool.submitWithdrawal(...)

## Question
Can core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx) reach BaseWithdrawPool.submitWithdrawal(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Use a callback-capable token or recipient around BaseWithdrawPool.submitWithdrawal(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Use a malicious token and withdrawal receiver to test whether Clearinghouse moves funds before all debits, utilization checks, and health checks are final.

# Q2076: Reentrancy or stale-state window at Verifier.requireValidTxSignatures(...)

## Question
Can core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) reach Verifier.requireValidTxSignatures(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Use a callback-capable token or recipient around Verifier.requireValidTxSignatures(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: Withdrawals must execute at most once per unique request and must not exceed the user’s withdrawable amount.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Use fee-on-transfer or callback-enabled test tokens to verify that fee accounting matches actual assets moved through the pool.

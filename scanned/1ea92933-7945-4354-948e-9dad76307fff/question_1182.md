# Q1182: Reentrancy or stale-state window at endpoint.depositCollateralWithReferral(...)

## Question
Can core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount) reach endpoint.depositCollateralWithReferral(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount)
- Entrypoint: User funds a DirectDepositV1 helper and triggers DirectDepositV1.creditDeposit(...).
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Use a callback-capable token or recipient around endpoint.depositCollateralWithReferral(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: Deposits must credit no more value than the helper actually transfers into protocol custody.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Use a fee-on-transfer, rebasing, or callback-capable token in a Hardhat test and compare helper balances versus protocol credit after creditDeposit(...).

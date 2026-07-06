# Q975: Arithmetic edge case in amount

## Question
Can attacker-controlled extremes of amount drive core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount)
- Entrypoint: User funds a DirectDepositV1 helper and triggers DirectDepositV1.creditDeposit(...).
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Fuzz amount around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount) mutates balances and risk state.
- Invariant to test: Helper-assisted asset wrapping and direct-deposit flows must not strand value, overcredit balances, or allow cross-token confusion.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Test repeated creditDeposit() and receive() flows around wrappedNative and ERC4626 wrapping to assert no stale approvals or double-credit paths exist.

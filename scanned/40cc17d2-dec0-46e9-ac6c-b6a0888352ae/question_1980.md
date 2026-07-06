# Q1980: Failure-handling mismatch after token.safeTransferFrom(...)

## Question
Can attacker-controlled failure behavior around token.safeTransferFrom(...) leave core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User calls BaseWithdrawPool.submitFastWithdrawal(...) directly with a transaction blob and signature set.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Force token.safeTransferFrom(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: Fee collection and token transfer paths must not allow double-claim, underpayment, overpayment, or reentrancy-driven balance corruption.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through withdrawal replay, double-claim, or pool insolvency.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.

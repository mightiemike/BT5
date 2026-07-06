# Q2284: Type confusion between signed intent and executed path

## Question
Can an attacker craft calldata or a signed payload so that core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) validates one semantic action but decodes or executes another semantic action with a different effect on balances, positions, recipients, or signers?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Cross-check the validated digest fields against the later decode/dispatch logic in core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx), especially where transaction type, appendix bits, recipient, or derived subaccount state influence execution.
- Invariant to test: Withdrawals must execute at most once per unique request and must not exceed the user’s withdrawable amount.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation via action-type confusion.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.

# Q530: Signature binding gap around sendTo

## Question
Can an unprivileged user reach core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount) through a normal Nado flow where the executed state change depends on sendTo, but the accepted signature or digest path fails to bind sendTo tightly enough to prevent a semantically different execution?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Mutate sendTo after signing while preserving every other signed component and see whether the same authorization still drives a different state transition through core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount).
- Invariant to test: Withdrawals must execute at most once per unique request and must not exceed the user’s withdrawable amount.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through withdrawal replay, double-claim, or pool insolvency.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.

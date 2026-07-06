# Q2164: Rounding leak through idx

## Question
Can repeated user-controlled updates around idx make core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User calls BaseWithdrawPool.submitFastWithdrawal(...) directly with a transaction blob and signature set.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving idx; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Withdrawals must execute at most once per unique request and must not exceed the user’s withdrawable amount.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.

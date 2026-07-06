# Q1102: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

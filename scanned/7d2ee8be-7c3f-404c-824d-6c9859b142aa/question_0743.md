# Q743: Cross-contract desync of minIdx

## Question
Can a normal user drive core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) so that minIdx is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Target the exact moment when core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) mutates minIdx and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Fee collection and token transfer paths must not allow double-claim, underpayment, overpayment, or reentrancy-driven balance corruption.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfers or stale-state withdrawal fulfillment.
- Fast validation: Use fee-on-transfer or callback-enabled test tokens to verify that fee accounting matches actual assets moved through the pool.

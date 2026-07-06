# Q887: Ordering dependency around withdrawal submission order

## Question
Can an attacker manipulate reachable call order so that core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) observes withdrawal submission order in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User calls BaseWithdrawPool.submitFastWithdrawal(...) directly with a transaction blob and signature set.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Reorder the same user actions around withdrawal submission order, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Fast-withdrawal signatures and idx tracking must bind the exact withdrawal semantics being paid out.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

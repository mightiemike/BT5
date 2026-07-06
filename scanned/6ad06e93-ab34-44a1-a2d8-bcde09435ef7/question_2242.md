# Q2242: Stale or double-applied clearinghouse

## Question
Can attacker-controlled sequencing make core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) consume stale clearinghouse or apply the same clearinghouse transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale clearinghouse before all related state is finalized.
- Invariant to test: Fast-withdrawal signatures and idx tracking must bind the exact withdrawal semantics being paid out.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation through malformed withdrawal payloads.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

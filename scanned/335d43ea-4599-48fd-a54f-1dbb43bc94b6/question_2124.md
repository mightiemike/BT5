# Q2124: Replay or cross-context reuse of sendTo

## Question
Can a signature or signed payload accepted by core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) be replayed in a different context where sendTo changes meaning, allowing the attacker to reuse valid authorization for a different economic effect?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User interacts with WithdrawPool through normal withdrawal and fast-withdrawal flows.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Try to replay the same signed bytes after mutating only the execution context for sendTo, including alternate product, queue, recipient, or isolated-subaccount conditions.
- Invariant to test: Fast-withdrawal signatures and idx tracking must bind the exact withdrawal semantics being paid out.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation through malformed withdrawal payloads.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

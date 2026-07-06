# Q1630: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures) leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures); then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

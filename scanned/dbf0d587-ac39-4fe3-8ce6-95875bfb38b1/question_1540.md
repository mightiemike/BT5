# Q1540: Pre-check versus post-effect mismatch

## Question
Can core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures) satisfy an authorization, health, limit, or utilization check before a later effect changes the underlying balance or risk inputs, leaving the final state outside the condition that was actually checked?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures)
- Entrypoint: User interacts with WithdrawPool through normal withdrawal and fast-withdrawal flows.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Locate every require/assert-style gate around core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures), then mutate the referenced balances, fees, or risk variables later in the same path and compare the checked pre-state to the committed post-state.
- Invariant to test: Safety checks must guard the final committed effect, not only an earlier intermediate state that becomes invalid before the transaction ends.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, liquidation bypass, or logic attack through check-effect mismatch.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

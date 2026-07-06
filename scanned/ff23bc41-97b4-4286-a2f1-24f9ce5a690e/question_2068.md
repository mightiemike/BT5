# Q2068: Recipient routing or sendTo confusion

## Question
Can attacker-controlled recipient fields make core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) pay the wrong recipient, let a linked signer redirect funds, or let a fast-withdrawal helper reinterpret sender-versus-sendTo semantics after signature verification?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User calls BaseWithdrawPool.submitFastWithdrawal(...) directly with a transaction blob and signature set.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Vary sendTo, sender-derived default recipients, V2 appendix fields, and fee-payer branches to see whether the authorized withdrawal destination can be changed without a new valid authorization.
- Invariant to test: Withdrawals must route funds only to the intended recipient derived from the exact authorized withdrawal semantics.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal or transaction manipulation that reroutes funds.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

# Q171: Chain, domain, or contract binding gap

## Question
Can authorization accepted by core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount) be replayed across a different chain, proxy implementation, verifying contract, or helper context because the signed domain does not fully match the execution domain?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount)
- Entrypoint: User interacts with WithdrawPool through normal withdrawal and fast-withdrawal flows.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Recreate the same signed payload under alternate chainId, proxy, helper, verifying-contract, or domain-separator contexts and check whether core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount) still accepts it for a different live execution surface.
- Invariant to test: Signed actions must bind the exact live Nado execution domain and must not survive a change in chain, contract, proxy, or helper context.
- Expected HackenProof impact: Critical/High: replay or unauthorized transaction through insufficient domain separation.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

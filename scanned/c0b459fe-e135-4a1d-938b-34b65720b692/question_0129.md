# Q129: Beneficiary routing default or zero-value coercion

## Question
Can core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount) fall back to a default recipient, default subaccount, zero address, or caller-derived beneficiary in a way that lets the attacker redirect value or settle against the wrong destination without explicitly authorizing it?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount)
- Entrypoint: User interacts with WithdrawPool through normal withdrawal and fast-withdrawal flows.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Force optional recipient fields, empty sendTo values, zero subaccounts, unset isolated mappings, or caller-derived defaults around core/contracts/BaseWithdrawPool.sol / fastWithdrawalFeeAmount(IERC20Base token, uint32 productId, uint128 amount) and compare who ultimately receives value or state updates.
- Invariant to test: Every value-moving action must resolve to exactly one intended beneficiary and must not silently substitute a different account or recipient.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, transfer, or account mutation through beneficiary confusion.
- Fast validation: Track pool token balance, fees, and markedIdxs through fast and normal withdrawals to assert exact one-time payment semantics.

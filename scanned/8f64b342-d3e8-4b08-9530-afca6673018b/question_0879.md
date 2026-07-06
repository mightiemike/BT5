# Q879: Rounding leak through previewDeposit(...) output

## Question
Can repeated user-controlled updates around previewDeposit(...) output make core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral)
- Entrypoint: User sends native tokens to the DirectDepositV1 receive() path or routes ERC4626 wrapping through ContractOwner helper flows.
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving previewDeposit(...) output; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Deposits must credit no more value than the helper actually transfers into protocol custody.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Use a fee-on-transfer, rebasing, or callback-capable token in a Hardhat test and compare helper balances versus protocol credit after creditDeposit(...).

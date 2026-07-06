# Q711: Dust-cycle extraction or min-threshold bypass

## Question
Can repeated tiny user-controlled operations through core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral) stay below a per-step threshold, rounding guard, fee floor, or min-size rule while still accumulating a meaningful balance, position, or withdrawal advantage over many iterations?

## Target
- File/function: core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral)
- Entrypoint: User funds a DirectDepositV1 helper and triggers DirectDepositV1.creditDeposit(...).
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Search for floor divisions, min-size exemptions, fee-on-first-fill logic, or first-deposit thresholds around core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral); then repeat the smallest admissible action until any measurable value leak or rule bypass appears.
- Invariant to test: Deposits must credit no more value than the helper actually transfers into protocol custody.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that extracts value by exploiting repeated micro-operations.
- Fast validation: Use a fee-on-transfer, rebasing, or callback-capable token in a Hardhat test and compare helper balances versus protocol credit after creditDeposit(...).

# Q795: Overcredit from non-standard token or helper accounting

## Question
Can attacker-controlled token behavior or helper timing make core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral) credit a larger deposit than the protocol actually receives, leaving later withdrawals or quote transfers to drain honest liquidity?

## Target
- File/function: core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral)
- Entrypoint: User funds a DirectDepositV1 helper and triggers DirectDepositV1.creditDeposit(...).
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Use fee-on-transfer, rebasing, previewDeposit mismatch, or callback behavior and compare actual token custody against the realized balance change caused by core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral).
- Invariant to test: Deposits must never create more protocol credit than the actual asset value received into custody.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through unauthorized deposit credit or pool insolvency.
- Fast validation: Use a fee-on-transfer, rebasing, or callback-capable token in a Hardhat test and compare helper balances versus protocol credit after creditDeposit(...).

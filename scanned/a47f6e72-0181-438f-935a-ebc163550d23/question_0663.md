# Q663: Cross-contract desync of endpoint

## Question
Can a normal user drive core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral) so that endpoint is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral)
- Entrypoint: User funds a DirectDepositV1 helper and triggers DirectDepositV1.creditDeposit(...).
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Target the exact moment when core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral) mutates endpoint and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Deposits must credit no more value than the helper actually transfers into protocol custody.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through overcredit, stranded helper balances, or helper-assisted withdrawal mismatch.
- Fast validation: Use a fee-on-transfer, rebasing, or callback-capable token in a Hardhat test and compare helper balances versus protocol credit after creditDeposit(...).

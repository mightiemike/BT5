# Q563: Overcredit from non-standard token or helper accounting

## Question
Can attacker-controlled token behavior or helper timing make core/contracts/ContractOwner.sol / creditDepositV1(bytes32 subaccount) credit a larger deposit than the protocol actually receives, leaving later withdrawals or quote transfers to drain honest liquidity?

## Target
- File/function: core/contracts/ContractOwner.sol / creditDepositV1(bytes32 subaccount)
- Entrypoint: User calls non-owner ContractOwner helper flows such as creditDepositV1(...), wrapVaultAsset(...), createDirectDepositV1(...), or replaceUsdcEWithUsdc(...).
- Attacker controls: subaccount, productId, helper call timing, ERC4626 preview output, token balances held by the direct-deposit helper
- Exploit idea: Use fee-on-transfer, rebasing, previewDeposit mismatch, or callback behavior and compare actual token custody against the realized balance change caused by core/contracts/ContractOwner.sol / creditDepositV1(bytes32 subaccount).
- Invariant to test: Deposits must never create more protocol credit than the actual asset value received into custody.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through unauthorized deposit credit or pool insolvency.
- Fast validation: Write a Hardhat test that calls the public helper functions against another user’s subaccount and assert no unauthorized asset movement or helper-state mutation occurs.

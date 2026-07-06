# Q1315: Overcredit from non-standard token or helper accounting

## Question
Can attacker-controlled token behavior or helper timing make core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory) credit a larger deposit than the protocol actually receives, leaving later withdrawals or quote transfers to drain honest liquidity?

## Target
- File/function: core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory)
- Entrypoint: User queues a transaction through Endpoint.submitSlowModeTransaction(...) and later executes it through Endpoint.executeSlowModeTransaction(...).
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Use fee-on-transfer, rebasing, previewDeposit mismatch, or callback behavior and compare actual token custody against the realized balance change caused by core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory).
- Invariant to test: Deposits must never create more protocol credit than the actual asset value received into custody.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through unauthorized deposit credit or pool insolvency.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.

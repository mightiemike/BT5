# Q548: Nominal-versus-realized asset mismatch

## Question
Can core/contracts/ContractOwner.sol / creditDepositV1(bytes32 subaccount) trust a nominal amount, preview amount, claimed amount, or signed amount that diverges from the assets actually transferred or the balances actually settled, creating unbacked credit or underpaid liabilities?

## Target
- File/function: core/contracts/ContractOwner.sol / creditDepositV1(bytes32 subaccount)
- Entrypoint: User calls non-owner ContractOwner helper flows such as creditDepositV1(...), wrapVaultAsset(...), createDirectDepositV1(...), or replaceUsdcEWithUsdc(...).
- Attacker controls: subaccount, productId, helper call timing, ERC4626 preview output, token balances held by the direct-deposit helper
- Exploit idea: Compare the user-controlled nominal amount against the realized token movement, internal balance delta, and downstream settlement effect caused by core/contracts/ContractOwner.sol / creditDepositV1(bytes32 subaccount), especially around fees, wrappers, and non-standard token behavior.
- Invariant to test: Internal accounting must track realized asset movement and must not mint credit or settle liabilities from nominal amounts alone.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through overcredit, underpayment, or hidden insolvency.
- Fast validation: Write a Hardhat test that calls the public helper functions against another user’s subaccount and assert no unauthorized asset movement or helper-state mutation occurs.

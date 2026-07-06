# Q1260: Nominal-versus-realized asset mismatch

## Question
Can core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory) trust a nominal amount, preview amount, claimed amount, or signed amount that diverges from the assets actually transferred or the balances actually settled, creating unbacked credit or underpaid liabilities?

## Target
- File/function: core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory)
- Entrypoint: User calls Endpoint.depositCollateralWithReferral(...) with a crafted subaccount or token amount.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Compare the user-controlled nominal amount against the realized token movement, internal balance delta, and downstream settlement effect caused by core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory), especially around fees, wrappers, and non-standard token behavior.
- Invariant to test: Internal accounting must track realized asset movement and must not mint credit or settle liabilities from nominal amounts alone.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through overcredit, underpayment, or hidden insolvency.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.

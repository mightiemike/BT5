# Q2803: Nominal-versus-realized asset mismatch

## Question
Can core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn) trust a nominal amount, preview amount, claimed amount, or signed amount that diverges from the assets actually transferred or the balances actually settled, creating unbacked credit or underpaid liabilities?

## Target
- File/function: core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn)
- Entrypoint: User deposits collateral through Endpoint and the call lands in Clearinghouse.depositCollateral(...).
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Compare the user-controlled nominal amount against the realized token movement, internal balance delta, and downstream settlement effect caused by core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn), especially around fees, wrappers, and non-standard token behavior.
- Invariant to test: Internal accounting must track realized asset movement and must not mint credit or settle liabilities from nominal amounts alone.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through overcredit, underpayment, or hidden insolvency.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.

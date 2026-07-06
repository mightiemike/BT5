# Q2971: Overcredit from non-standard token or helper accounting

## Question
Can attacker-controlled token behavior or helper timing make core/contracts/Clearinghouse.sol / depositInsurance(bytes calldata transaction) credit a larger deposit than the protocol actually receives, leaving later withdrawals or quote transfers to drain honest liquidity?

## Target
- File/function: core/contracts/Clearinghouse.sol / depositInsurance(bytes calldata transaction)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Use fee-on-transfer, rebasing, previewDeposit mismatch, or callback behavior and compare actual token custody against the realized balance change caused by core/contracts/Clearinghouse.sol / depositInsurance(bytes calldata transaction).
- Invariant to test: Deposits must never create more protocol credit than the actual asset value received into custody.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through unauthorized deposit credit or pool insolvency.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.

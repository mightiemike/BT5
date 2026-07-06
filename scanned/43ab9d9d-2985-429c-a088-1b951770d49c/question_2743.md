# Q2743: Cross-contract desync of engineByType

## Question
Can a normal user drive core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn) so that engineByType is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Target the exact moment when core/contracts/Clearinghouse.sol / depositCollateral(IEndpoint.DepositCollateral calldata txn) mutates engineByType and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: External asset transfers must not happen in a way that leaves user balances or protocol balances inconsistent after failure or reentrancy.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, insolvency, or invalid liquidation/settlement outcomes.
- Fast validation: Use a malicious token and withdrawal receiver to test whether Clearinghouse moves funds before all debits, utilization checks, and health checks are final.

# Q3491: Reentrancy or stale-state window at clearinghouseLiq.delegatecall(...)

## Question
Can core/contracts/Clearinghouse.sol / nlpProfitShare(bytes32 poolSubaccount, bytes32 recipient, uint128 amount) reach clearinghouseLiq.delegatecall(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/Clearinghouse.sol / nlpProfitShare(bytes32 poolSubaccount, bytes32 recipient, uint128 amount)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Use a callback-capable token or recipient around clearinghouseLiq.delegatecall(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Use a malicious token and withdrawal receiver to test whether Clearinghouse moves funds before all debits, utilization checks, and health checks are final.

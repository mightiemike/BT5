# Q3123: Temporary solvency window across sequential updates

## Question
Can core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18) apply a sequence of balance, funding, fee, or health updates in an order that lets the attacker briefly appear solvent and extract value before the final liability is applied?

## Target
- File/function: core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Search for sequences where realized credits are applied before liabilities, funding, borrow costs, or fee debits around core/contracts/Clearinghouse.sol / forceRebalanceNlpPool(IEndpoint.NlpPool[] calldata nlpPools, int128[] calldata nlpPoolRebalanceX18); then attempt withdraw, transfer, or match operations inside that intermediate window.
- Invariant to test: A user must never be able to spend, withdraw, or avoid liquidation using equity that exists only during an intermediate update order.
- Expected HackenProof impact: Critical/High: logic attack causing unauthorized withdrawal, liquidation bypass, or system bad debt.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.

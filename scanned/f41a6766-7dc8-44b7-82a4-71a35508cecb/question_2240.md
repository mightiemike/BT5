# Q2240: Temporary solvency window across sequential updates

## Question
Can core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner) apply a sequence of balance, funding, fee, or health updates in an order that lets the attacker briefly appear solvent and extract value before the final liability is applied?

## Target
- File/function: core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Search for sequences where realized credits are applied before liabilities, funding, borrow costs, or fee debits around core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner); then attempt withdraw, transfer, or match operations inside that intermediate window.
- Invariant to test: A user must never be able to spend, withdraw, or avoid liquidation using equity that exists only during an intermediate update order.
- Expected HackenProof impact: Critical/High: logic attack causing unauthorized withdrawal, liquidation bypass, or system bad debt.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

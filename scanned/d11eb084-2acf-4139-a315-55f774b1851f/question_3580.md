# Q3580: Spread or encoded-product aliasing

## Question
Can encoded spread state, composite product IDs, or product-bitmaps around core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn) alias to a different exposure than the health, pricing, or liquidation logic assumes, letting the attacker hide or reshape risk?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Fuzz every encoded spread leg, bitmap, and product-ID composition that reaches core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn), then compare the exposure seen by matching, health, settlement, and liquidation logic.
- Invariant to test: A user must not create or close isolated subaccounts in a way that steals margin, reuses signatures, or desynchronizes parent-child balances.
- Expected HackenProof impact: Critical/High: logic attack causing hidden liabilities, wrong liquidation behavior, or unauthorized balance mutation through product aliasing.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

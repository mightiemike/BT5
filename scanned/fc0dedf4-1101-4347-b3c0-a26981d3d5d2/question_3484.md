# Q3484: Chain, domain, or contract binding gap

## Question
Can authorization accepted by core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn) be replayed across a different chain, proxy implementation, verifying contract, or helper context because the signed domain does not fully match the execution domain?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
- Entrypoint: User later closes, claims, or settles through order-driven exchange flows that mutate OffchainExchange state.
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Recreate the same signed payload under alternate chainId, proxy, helper, verifying-contract, or domain-separator contexts and check whether core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn) still accepts it for a different live execution surface.
- Invariant to test: Signed actions must bind the exact live Nado execution domain and must not survive a change in chain, contract, proxy, or helper context.
- Expected HackenProof impact: Critical/High: replay or unauthorized transaction through insufficient domain separation.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

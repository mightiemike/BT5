# Q3496: Cross-engine conservation break

## Question
Can a reachable path through core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn) change spot balances, perp balances, insurance, collected fees, or availableSettle in a way that makes the combined system value drift after a complete trade, withdrawal, settlement, or liquidation cycle?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Model the full before/after state across spot, perp, clearinghouse, withdraw pool, builder-fee, and insurance accounting around core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn); then look for any delta that is not explained by an explicit fee or transfer.
- Invariant to test: Combined spot, perp, and clearinghouse accounting must conserve value except for explicit fees and real token movements.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, insolvency, or hidden value leakage across engines.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

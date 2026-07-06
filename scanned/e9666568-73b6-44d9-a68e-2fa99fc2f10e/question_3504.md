# Q3504: Failure-handling mismatch after perpEngine.updateBalance(...)

## Question
Can attacker-controlled failure behavior around perpEngine.updateBalance(...) leave core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Force perpEngine.updateBalance(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: A user must not create or close isolated subaccounts in a way that steals margin, reuses signatures, or desynchronizes parent-child balances.
- Expected HackenProof impact: Critical/High: unauthorized order execution or transaction manipulation outside signed order intent.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

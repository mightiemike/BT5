# Q2980: Failure-handling mismatch after clearinghouse.getEngineByProduct(...)

## Question
Can attacker-controlled failure behavior around clearinghouse.getEngineByProduct(...) leave core/contracts/OffchainExchange.sol / tryCloseIsolatedSubaccount(bytes32 subaccount) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/OffchainExchange.sol / tryCloseIsolatedSubaccount(bytes32 subaccount)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Force clearinghouse.getEngineByProduct(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: A user must not create or close isolated subaccounts in a way that steals margin, reuses signatures, or desynchronizes parent-child balances.
- Expected HackenProof impact: Critical/High: unauthorized order execution or transaction manipulation outside signed order intent.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

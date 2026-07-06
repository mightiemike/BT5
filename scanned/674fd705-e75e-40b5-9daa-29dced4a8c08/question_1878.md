# Q1878: Fill tracking mismatch across partial fills or cancellations

## Question
Can a user exploit core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner) so that partially filled, isolated, or builder-tagged orders update fill state under one digest interpretation but execute under another, allowing extra fills, stale fills, or wrong-account settlement?

## Target
- File/function: core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Stress partial fills, digestToSubaccount rewrites, builder fee metadata, and expiration windows; compare filledAmounts and final balances after repeated matching attempts.
- Invariant to test: An order must execute at most once up to its intended remaining quantity on the intended sender, market, side, and expiry.
- Expected HackenProof impact: Critical/High: unauthorized order execution or transaction manipulation outside signed maker/taker intent.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

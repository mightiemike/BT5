# Q2177: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner) cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: A user must not create or close isolated subaccounts in a way that steals margin, reuses signatures, or desynchronizes parent-child balances.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

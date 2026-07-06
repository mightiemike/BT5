# Q3352: Beneficiary routing default or zero-value coercion

## Question
Can core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize) fall back to a default recipient, default subaccount, zero address, or caller-derived beneficiary in a way that lets the attacker redirect value or settle against the wrong destination without explicitly authorizing it?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Force optional recipient fields, empty sendTo values, zero subaccounts, unset isolated mappings, or caller-derived defaults around core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize) and compare who ultimately receives value or state updates.
- Invariant to test: Every value-moving action must resolve to exactly one intended beneficiary and must not silently substitute a different account or recipient.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, transfer, or account mutation through beneficiary confusion.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

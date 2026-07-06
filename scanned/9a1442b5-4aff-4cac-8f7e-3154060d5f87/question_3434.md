# Q3434: Shared key, index, or mapping-collision confusion

## Question
Can attacker-controlled identifiers reaching core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize) collide in a shared mapping, bitmap, queue index, digest bucket, or derived storage key so that one user’s action overwrites, unlocks, or consumes another user’s state?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Search for every derived storage key, bitmap slot, queue index, digest map, or hash bucket touched by core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize); then try to construct two economically different actions that land on the same storage location.
- Invariant to test: Distinct users, subaccounts, orders, withdrawals, products, and queue items must never alias the same live state slot unless they are intentionally the same object.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or loss of funds through state-key collision.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

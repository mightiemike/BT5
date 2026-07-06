# Q2706: Subaccount authorization drift across derived identities

## Question
Can an unprivileged user drive core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order) with one sender or subaccount identity at validation time but a different effective sender or subaccount identity at execution time, causing state to mutate for the wrong account?

## Target
- File/function: core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Trace every conversion between address, bytes32 sender, linked signer, parent subaccount, isolated subaccount, and derived recipient around core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order); then try to keep validation attached to one identity while execution lands on another.
- Invariant to test: Only the exact authorized account, subaccount, or linked signer should be able to mutate that account’s balances, positions, orders, or withdrawals.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

# Q3338: Type confusion between signed intent and executed path

## Question
Can an attacker craft calldata or a signed payload so that core/contracts/OffchainExchange.sol / updateFeeTier(address user, uint32 newTier) validates one semantic action but decodes or executes another semantic action with a different effect on balances, positions, recipients, or signers?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateFeeTier(address user, uint32 newTier)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Cross-check the validated digest fields against the later decode/dispatch logic in core/contracts/OffchainExchange.sol / updateFeeTier(address user, uint32 newTier), especially where transaction type, appendix bits, recipient, or derived subaccount state influence execution.
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation via action-type confusion.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

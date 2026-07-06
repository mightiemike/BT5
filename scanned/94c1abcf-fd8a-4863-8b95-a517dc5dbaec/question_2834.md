# Q2834: Partial batch progress without full rollback

## Question
Can a loop, queue, or multi-step batch around core/contracts/OffchainExchange.sol / matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn) make economic progress on early items even though a later item fails, leaving fill state, claim state, fees, or balances inconsistent with an all-or-nothing user assumption?

## Target
- File/function: core/contracts/OffchainExchange.sol / matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Construct a mixed-validity batch or queue sequence through core/contracts/OffchainExchange.sol / matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn), force one later element to fail, and compare whether earlier state changes remain committed in a way that can be exploited or replayed.
- Invariant to test: Batched or queued user actions must either preserve consistent partial-progress rules or prevent attackers from extracting value from early-commit and late-fail combinations.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through inconsistent partial progress handling.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

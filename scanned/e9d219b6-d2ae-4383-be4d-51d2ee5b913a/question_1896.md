# Q1896: Global accumulator bleed across users or products

## Question
Can attacker-controlled actions through core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner) push a shared accumulator such as fees, insurance, funding, utilization, queue counters, or collected balances in a way that later lets the attacker redeem, avoid, or shift value that should belong to another user or product?

## Target
- File/function: core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Track every shared accumulator touched before and after core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner), then interleave two users or two products and see whether the second actor can benefit from state that the first actor should have exclusively paid for or earned.
- Invariant to test: Shared protocol accumulators must remain correctly partitioned by user, product, pool, and request semantics.
- Expected HackenProof impact: Critical/High: loss of funds or logic attack through value bleed across shared accounting buckets.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

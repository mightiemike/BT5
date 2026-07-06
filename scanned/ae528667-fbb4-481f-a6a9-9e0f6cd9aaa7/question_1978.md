# Q1978: Global accumulator bleed across users or products

## Question
Can attacker-controlled actions through core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta) push a shared accumulator such as fees, insurance, funding, utilization, queue counters, or collected balances in a way that later lets the attacker redeem, avoid, or shift value that should belong to another user or product?

## Target
- File/function: core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Track every shared accumulator touched before and after core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta), then interleave two users or two products and see whether the second actor can benefit from state that the first actor should have exclusively paid for or earned.
- Invariant to test: Shared protocol accumulators must remain correctly partitioned by user, product, pool, and request semantics.
- Expected HackenProof impact: Critical/High: loss of funds or logic attack through value bleed across shared accounting buckets.
- Fast validation: Write a Hardhat model test for open/close/flip/settle/socialize sequences and compare realized and unrealized PnL against a reference implementation.

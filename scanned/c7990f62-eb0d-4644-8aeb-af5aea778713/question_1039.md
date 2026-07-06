# Q1039: Stale cache or memoized-state window

## Question
Can core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount) read a cached market, health, fee, builder, funding, or balance value that becomes stale before the rest of execution finishes, so later steps act on assumptions that are no longer true?

## Target
- File/function: core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Identify any state snapshot, cached struct, or copied market state used across multiple branches in core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount); then mutate the underlying live state through a reachable interleaving and compare the cached result to fresh reads.
- Invariant to test: A cached or memoized view of state must not remain valid across later user-reachable transitions that can change the economic outcome.
- Expected HackenProof impact: Critical/High: reordering or logic attack through stale cached state.
- Fast validation: Write a Hardhat model test for open/close/flip/settle/socialize sequences and compare realized and unrealized PnL against a reference implementation.

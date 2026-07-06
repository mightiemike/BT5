# Q1402: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/PerpEngine.sol / settlePnl(bytes32 subaccount, uint256 productIds) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/PerpEngine.sol / settlePnl(bytes32 subaccount, uint256 productIds)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/PerpEngine.sol / settlePnl(bytes32 subaccount, uint256 productIds) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Fuzz productIds bitmaps and signed position deltas and assert settlement cannot be applied twice or to the wrong market.

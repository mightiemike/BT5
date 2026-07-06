# Q1307: Ordering dependency around settlement order

## Question
Can an attacker manipulate reachable call order so that core/contracts/PerpEngine.sol / settlePnl(bytes32 subaccount, uint256 productIds) observes settlement order in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/PerpEngine.sol / settlePnl(bytes32 subaccount, uint256 productIds)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Reorder the same user actions around settlement order, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Perp positions, vQuote, settlement state, and socialized losses must conserve value across open, close, flip, settle, and liquidation flows.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Fuzz productIds bitmaps and signed position deltas and assert settlement cannot be applied twice or to the wrong market.

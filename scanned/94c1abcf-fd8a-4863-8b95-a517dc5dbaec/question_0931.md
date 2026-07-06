# Q931: Rounding leak through amountDelta

## Question
Can repeated user-controlled updates around amountDelta make core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving amountDelta; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Perp positions, vQuote, settlement state, and socialized losses must conserve value across open, close, flip, settle, and liquidation flows.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write a Hardhat model test for open/close/flip/settle/socialize sequences and compare realized and unrealized PnL against a reference implementation.

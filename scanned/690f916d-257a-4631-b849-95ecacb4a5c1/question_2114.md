# Q2114: Spread or encoded-product aliasing

## Question
Can encoded spread state, composite product IDs, or product-bitmaps around core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta) alias to a different exposure than the health, pricing, or liquidation logic assumes, letting the attacker hide or reshape risk?

## Target
- File/function: core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Fuzz every encoded spread leg, bitmap, and product-ID composition that reaches core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta), then compare the exposure seen by matching, health, settlement, and liquidation logic.
- Invariant to test: Perp positions, vQuote, settlement state, and socialized losses must conserve value across open, close, flip, settle, and liquidation flows.
- Expected HackenProof impact: Critical/High: logic attack causing hidden liabilities, wrong liquidation behavior, or unauthorized balance mutation through product aliasing.
- Fast validation: Fuzz productIds bitmaps and signed position deltas and assert settlement cannot be applied twice or to the wrong market.

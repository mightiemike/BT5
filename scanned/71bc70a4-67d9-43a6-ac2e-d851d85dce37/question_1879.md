# Q1879: Arithmetic edge case in fundingPerShare

## Question
Can attacker-controlled extremes of fundingPerShare drive core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Fuzz fundingPerShare around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/PerpEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 vQuoteDelta) mutates balances and risk state.
- Invariant to test: Perp positions, vQuote, settlement state, and socialized losses must conserve value across open, close, flip, settle, and liquidation flows.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Fuzz productIds bitmaps and signed position deltas and assert settlement cannot be applied twice or to the wrong market.

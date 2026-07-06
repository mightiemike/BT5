# Q847: Liability saturation or sign-flip saturation gap

## Question
Can attacker-controlled liabilities around core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount) hit a max, min, abs, or sign-flip boundary where debt stops growing correctly, collateral stops shrinking correctly, or a penalty saturates before the real exposure does?

## Target
- File/function: core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Push liabilities, borrows, negative PnL, spread exposures, and liquidation amounts toward every numeric boundary used around core/contracts/PerpEngine.sol / getSettlementState(uint32 productId, bytes32 subaccount); then compare the realized exposure to the mathematically expected exposure.
- Invariant to test: Debt, liability, and penalty accounting must remain monotonic and must not saturate early in a way that benefits the attacker.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack causing hidden liabilities or under-penalized bad debt.
- Fast validation: Fuzz productIds bitmaps and signed position deltas and assert settlement cannot be applied twice or to the wrong market.

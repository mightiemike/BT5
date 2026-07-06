# Q1600: Dust-cycle extraction or min-threshold bypass

## Question
Can repeated tiny user-controlled operations through core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance) stay below a per-step threshold, rounding guard, fee floor, or min-size rule while still accumulating a meaningful balance, position, or withdrawal advantage over many iterations?

## Target
- File/function: core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Search for floor divisions, min-size exemptions, fee-on-first-fill logic, or first-deposit thresholds around core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance); then repeat the smallest admissible action until any measurable value leak or rule bypass appears.
- Invariant to test: Perp positions, vQuote, settlement state, and socialized losses must conserve value across open, close, flip, settle, and liquidation flows.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that extracts value by exploiting repeated micro-operations.
- Fast validation: Write a Hardhat model test for open/close/flip/settle/socialize sequences and compare realized and unrealized PnL against a reference implementation.

# Q3207: Spread or encoded-product aliasing

## Question
Can encoded spread state, composite product IDs, or product-bitmaps around core/contracts/Clearinghouse.sol / getHealth(bytes32 subaccount, IProductEngine.HealthType healthType) alias to a different exposure than the health, pricing, or liquidation logic assumes, letting the attacker hide or reshape risk?

## Target
- File/function: core/contracts/Clearinghouse.sol / getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Fuzz every encoded spread leg, bitmap, and product-ID composition that reaches core/contracts/Clearinghouse.sol / getHealth(bytes32 subaccount, IProductEngine.HealthType healthType), then compare the exposure seen by matching, health, settlement, and liquidation logic.
- Invariant to test: External asset transfers must not happen in a way that leaves user balances or protocol balances inconsistent after failure or reentrancy.
- Expected HackenProof impact: Critical/High: logic attack causing hidden liabilities, wrong liquidation behavior, or unauthorized balance mutation through product aliasing.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.

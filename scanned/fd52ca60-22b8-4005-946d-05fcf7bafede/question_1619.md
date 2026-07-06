# Q1619: Spread or encoded-product aliasing

## Question
Can encoded spread state, composite product IDs, or product-bitmaps around core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 quoteDelta) alias to a different exposure than the health, pricing, or liquidation logic assumes, letting the attacker hide or reshape risk?

## Target
- File/function: core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 quoteDelta)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Fuzz every encoded spread leg, bitmap, and product-ID composition that reaches core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 quoteDelta), then compare the exposure seen by matching, health, settlement, and liquidation logic.
- Invariant to test: Spot balances, borrow/deposit multipliers, and utilization checks must conserve value across deposits, withdrawals, fills, NLP, and liquidation.
- Expected HackenProof impact: Critical/High: logic attack causing hidden liabilities, wrong liquidation behavior, or unauthorized balance mutation through product aliasing.
- Fast validation: Fuzz signed amounts, product IDs, and zero-crossing transitions around SpotEngine.updateBalance(...) and assert no unbacked credit appears.

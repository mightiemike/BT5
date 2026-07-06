# Q1187: Liability saturation or sign-flip saturation gap

## Question
Can attacker-controlled liabilities around core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta) hit a max, min, abs, or sign-flip boundary where debt stops growing correctly, collateral stops shrinking correctly, or a penalty saturates before the real exposure does?

## Target
- File/function: core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Push liabilities, borrows, negative PnL, spread exposures, and liquidation amounts toward every numeric boundary used around core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta); then compare the realized exposure to the mathematically expected exposure.
- Invariant to test: Debt, liability, and penalty accounting must remain monotonic and must not saturate early in a way that benefits the attacker.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack causing hidden liabilities or under-penalized bad debt.
- Fast validation: Fuzz signed amounts, product IDs, and zero-crossing transitions around SpotEngine.updateBalance(...) and assert no unbacked credit appears.

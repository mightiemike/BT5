# Q1979: Liability saturation or sign-flip saturation gap

## Question
Can attacker-controlled liabilities around core/contracts/BaseEngine.sol / getRisk(uint32 productId) hit a max, min, abs, or sign-flip boundary where debt stops growing correctly, collateral stops shrinking correctly, or a penalty saturates before the real exposure does?

## Target
- File/function: core/contracts/BaseEngine.sol / getRisk(uint32 productId)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Push liabilities, borrows, negative PnL, spread exposures, and liquidation amounts toward every numeric boundary used around core/contracts/BaseEngine.sol / getRisk(uint32 productId); then compare the realized exposure to the mathematically expected exposure.
- Invariant to test: Debt, liability, and penalty accounting must remain monotonic and must not saturate early in a way that benefits the attacker.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack causing hidden liabilities or under-penalized bad debt.
- Fast validation: Fuzz nonZeroBalances transitions around zero-crossing updates and assert no product bit remains stale across withdraw, fill, or liquidation flows.

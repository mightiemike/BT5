# Q1321: Arithmetic edge case in risk weights

## Question
Can attacker-controlled extremes of risk weights drive core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Fuzz risk weights around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType) mutates balances and risk state.
- Invariant to test: Bitmap iteration, health contribution, and risk-weight application must not skip positions, misprice risk, or let attacker-controlled state hide liabilities.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Fuzz nonZeroBalances transitions around zero-crossing updates and assert no product bit remains stale across withdraw, fill, or liquidation flows.

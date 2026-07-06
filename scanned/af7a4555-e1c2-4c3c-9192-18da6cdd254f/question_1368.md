# Q1368: Cross-engine conservation break

## Question
Can a reachable path through core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType) change spot balances, perp balances, insurance, collected fees, or availableSettle in a way that makes the combined system value drift after a complete trade, withdrawal, settlement, or liquidation cycle?

## Target
- File/function: core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Model the full before/after state across spot, perp, clearinghouse, withdraw pool, builder-fee, and insurance accounting around core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType); then look for any delta that is not explained by an explicit fee or transfer.
- Invariant to test: Combined spot, perp, and clearinghouse accounting must conserve value except for explicit fees and real token movements.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, insolvency, or hidden value leakage across engines.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.

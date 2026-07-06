# Q1575: Temporary solvency window across sequential updates

## Question
Can core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType) apply a sequence of balance, funding, fee, or health updates in an order that lets the attacker briefly appear solvent and extract value before the final liability is applied?

## Target
- File/function: core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Search for sequences where realized credits are applied before liabilities, funding, borrow costs, or fee debits around core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType); then attempt withdraw, transfer, or match operations inside that intermediate window.
- Invariant to test: A user must never be able to spend, withdraw, or avoid liquidation using equity that exists only during an intermediate update order.
- Expected HackenProof impact: Critical/High: logic attack causing unauthorized withdrawal, liquidation bypass, or system bad debt.
- Fast validation: Fuzz nonZeroBalances transitions around zero-crossing updates and assert no product bit remains stale across withdraw, fill, or liquidation flows.

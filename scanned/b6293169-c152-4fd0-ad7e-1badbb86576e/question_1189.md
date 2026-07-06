# Q1189: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/BaseEngine.sol / _processBitmapChunk(uint256 bitmapChunk, uint32 bitmapIndex, bytes32 subaccount, IProductEngine.HealthType healthType) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/BaseEngine.sol / _processBitmapChunk(uint256 bitmapChunk, uint32 bitmapIndex, bytes32 subaccount, IProductEngine.HealthType healthType)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/BaseEngine.sol / _processBitmapChunk(uint256 bitmapChunk, uint32 bitmapIndex, bytes32 subaccount, IProductEngine.HealthType healthType) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Fuzz nonZeroBalances transitions around zero-crossing updates and assert no product bit remains stale across withdraw, fill, or liquidation flows.

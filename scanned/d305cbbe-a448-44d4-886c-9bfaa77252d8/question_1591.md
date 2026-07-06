# Q1591: Cross-engine conservation break

## Question
Can a reachable path through core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance) change spot balances, perp balances, insurance, collected fees, or availableSettle in a way that makes the combined system value drift after a complete trade, withdrawal, settlement, or liquidation cycle?

## Target
- File/function: core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Model the full before/after state across spot, perp, clearinghouse, withdraw pool, builder-fee, and insurance accounting around core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance); then look for any delta that is not explained by an explicit fee or transfer.
- Invariant to test: Combined spot, perp, and clearinghouse accounting must conserve value except for explicit fees and real token movements.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, insolvency, or hidden value leakage across engines.
- Fast validation: Write a Hardhat model test for open/close/flip/settle/socialize sequences and compare realized and unrealized PnL against a reference implementation.

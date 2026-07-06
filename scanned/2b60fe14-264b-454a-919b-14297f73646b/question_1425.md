# Q1425: Health-check bypass through stale or incomplete risk inputs

## Question
Can an attacker reach core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount) with a portfolio shape that hides a liability, spread leg, borrowed spot, or unsettled perp loss from the health calculation used by the calling flow?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount)
- Entrypoint: User submits a signed liquidation transaction that EndpointTx routes into Clearinghouse.liquidateSubaccount(...), which delegatecalls ClearinghouseLiq.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Build portfolios spanning spot, perp, spread, isolated, and NLP balances, then compare explicit risk aggregation against the health result consumed around core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount).
- Invariant to test: Health checks must include every reachable liability and must not let a user withdraw, transfer, or avoid liquidation with non-existent equity.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, unauthorized withdrawal, or liquidation bypass.
- Fast validation: Fuzz quote balances, spread products, and product iteration order to test whether liquidation leaves insurance, balances, and open interest conserved.

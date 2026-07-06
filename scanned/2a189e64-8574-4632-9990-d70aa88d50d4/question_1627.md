# Q1627: Health-check bypass through stale or incomplete risk inputs

## Question
Can an attacker reach core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance) with a portfolio shape that hides a liability, spread leg, borrowed spot, or unsettled perp loss from the health calculation used by the calling flow?

## Target
- File/function: core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Build portfolios spanning spot, perp, spread, isolated, and NLP balances, then compare explicit risk aggregation against the health result consumed around core/contracts/PerpEngine.sol / socializeSubaccount(bytes32 subaccount, int128 insurance).
- Invariant to test: Health checks must include every reachable liability and must not let a user withdraw, transfer, or avoid liquidation with non-existent equity.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, unauthorized withdrawal, or liquidation bypass.
- Fast validation: Fuzz productIds bitmaps and signed position deltas and assert settlement cannot be applied twice or to the wrong market.

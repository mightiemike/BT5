# Q1799: Health-check bypass through stale or incomplete risk inputs

## Question
Can an attacker reach core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance) with a portfolio shape that hides a liability, spread leg, borrowed spot, or unsettled perp loss from the health calculation used by the calling flow?

## Target
- File/function: core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Build portfolios spanning spot, perp, spread, isolated, and NLP balances, then compare explicit risk aggregation against the health result consumed around core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance).
- Invariant to test: Health checks must include every reachable liability and must not let a user withdraw, transfer, or avoid liquidation with non-existent equity.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, unauthorized withdrawal, or liquidation bypass.
- Fast validation: Fuzz signed amounts, product IDs, and zero-crossing transitions around SpotEngine.updateBalance(...) and assert no unbacked credit appears.

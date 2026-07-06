# Q444: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
- Entrypoint: User reaches PerpEngineState calculations through matching, liquidation, settlement, and health-check flows.
- Attacker controls: productId, amount, vQuoteBalance, funding index inputs, priceX18
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: Perp balance state, funding accrual, and PnL computation must remain internally consistent and conserved through every reachable state transition.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Fuzz balance states and funding deltas near zero, max leverage, and sign flips while comparing PerpEngineState outputs to a model implementation.

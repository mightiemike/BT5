# Q1791: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/BaseEngine.sol / getHealthContribution(bytes32 subaccount, IProductEngine.HealthType healthType) cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/BaseEngine.sol / getHealthContribution(bytes32 subaccount, IProductEngine.HealthType healthType)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: Bitmap iteration, health contribution, and risk-weight application must not skip positions, misprice risk, or let attacker-controlled state hide liabilities.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.

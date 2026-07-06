# Q495: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/SpotEngine.sol / getNlpUnlockedBalance(bytes32 subaccount) cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/SpotEngine.sol / getNlpUnlockedBalance(bytes32 subaccount)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: Spot balances, borrow/deposit multipliers, and utilization checks must conserve value across deposits, withdrawals, fills, NLP, and liquidation.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Write invariants that compare spot balances, actual token custody, and utilization after every reachable deposit/withdraw/fill/NLP transition.

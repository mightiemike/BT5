# Q167: Rounding leak through fixed-point scaling

## Question
Can repeated user-controlled updates around fixed-point scaling make core/contracts/libraries/MathSD21x18.sol / module-level logic round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/libraries/MathSD21x18.sol / module-level logic
- Entrypoint: User reaches this library through production callers in Endpoint, Clearinghouse, engines, OffchainExchange, WithdrawPool, or Airdrop.
- Attacker controls: signed and unsigned numeric edge cases, decimals, amounts, product IDs, subaccount encoding, ERC20 return data
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving fixed-point scaling; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Math, encoding, transfer, and risk helpers must not let attacker-controlled inputs corrupt balances, positions, signatures, or isolation semantics.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Build a focused fuzz harness around each helper and its production callers, asserting identical semantics against a simple reference implementation.

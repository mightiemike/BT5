# Q273: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/libraries/RiskHelper.sol / module-level logic cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/libraries/RiskHelper.sol / module-level logic
- Entrypoint: User reaches this library through production callers in Endpoint, Clearinghouse, engines, OffchainExchange, WithdrawPool, or Airdrop.
- Attacker controls: signed and unsigned numeric edge cases, decimals, amounts, product IDs, subaccount encoding, ERC20 return data
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: Math, encoding, transfer, and risk helpers must not let attacker-controlled inputs corrupt balances, positions, signatures, or isolation semantics.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Build a focused fuzz harness around each helper and its production callers, asserting identical semantics against a simple reference implementation.

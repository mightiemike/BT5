# Q62: Arithmetic edge case in product IDs

## Question
Can attacker-controlled extremes of product IDs drive core/contracts/libraries/MathSD21x18.sol / module-level logic into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/libraries/MathSD21x18.sol / module-level logic
- Entrypoint: User reaches this library through production callers in Endpoint, Clearinghouse, engines, OffchainExchange, WithdrawPool, or Airdrop.
- Attacker controls: signed and unsigned numeric edge cases, decimals, amounts, product IDs, subaccount encoding, ERC20 return data
- Exploit idea: Fuzz product IDs around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/libraries/MathSD21x18.sol / module-level logic mutates balances and risk state.
- Invariant to test: Math, encoding, transfer, and risk helpers must not let attacker-controlled inputs corrupt balances, positions, signatures, or isolation semantics.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Build a focused fuzz harness around each helper and its production callers, asserting identical semantics against a simple reference implementation.

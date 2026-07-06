# Q363: Arithmetic edge case in int128 edges

## Question
Can attacker-controlled extremes of int128 edges drive core/contracts/libraries/ERC20Helper.sol / safeTransferFrom(IERC20Base self, address from, address to, uint256 amount) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/libraries/ERC20Helper.sol / safeTransferFrom(IERC20Base self, address from, address to, uint256 amount)
- Entrypoint: User reaches this library through production callers in Endpoint, Clearinghouse, engines, OffchainExchange, WithdrawPool, or Airdrop.
- Attacker controls: signed and unsigned numeric edge cases, decimals, amounts, product IDs, subaccount encoding, ERC20 return data
- Exploit idea: Fuzz int128 edges around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/libraries/ERC20Helper.sol / safeTransferFrom(IERC20Base self, address from, address to, uint256 amount) mutates balances and risk state.
- Invariant to test: Math, encoding, transfer, and risk helpers must not let attacker-controlled inputs corrupt balances, positions, signatures, or isolation semantics.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Build a focused fuzz harness around each helper and its production callers, asserting identical semantics against a simple reference implementation.

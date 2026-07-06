# Q147: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/libraries/RiskHelper.sol / module-level logic leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/libraries/RiskHelper.sol / module-level logic
- Entrypoint: User reaches this library through production callers in Endpoint, Clearinghouse, engines, OffchainExchange, WithdrawPool, or Airdrop.
- Attacker controls: signed and unsigned numeric edge cases, decimals, amounts, product IDs, subaccount encoding, ERC20 return data
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/libraries/RiskHelper.sol / module-level logic; then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Build a focused fuzz harness around each helper and its production callers, asserting identical semantics against a simple reference implementation.

# Q252: Shared key, index, or mapping-collision confusion

## Question
Can attacker-controlled identifiers reaching core/contracts/libraries/RiskHelper.sol / module-level logic collide in a shared mapping, bitmap, queue index, digest bucket, or derived storage key so that one user’s action overwrites, unlocks, or consumes another user’s state?

## Target
- File/function: core/contracts/libraries/RiskHelper.sol / module-level logic
- Entrypoint: User reaches this library through production callers in Endpoint, Clearinghouse, engines, OffchainExchange, WithdrawPool, or Airdrop.
- Attacker controls: signed and unsigned numeric edge cases, decimals, amounts, product IDs, subaccount encoding, ERC20 return data
- Exploit idea: Search for every derived storage key, bitmap slot, queue index, digest map, or hash bucket touched by core/contracts/libraries/RiskHelper.sol / module-level logic; then try to construct two economically different actions that land on the same storage location.
- Invariant to test: Distinct users, subaccounts, orders, withdrawals, products, and queue items must never alias the same live state slot unless they are intentionally the same object.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or loss of funds through state-key collision.
- Fast validation: Build a focused fuzz harness around each helper and its production callers, asserting identical semantics against a simple reference implementation.

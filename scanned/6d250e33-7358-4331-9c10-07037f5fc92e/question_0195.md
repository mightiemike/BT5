# Q195: Shared key, index, or mapping-collision confusion

## Question
Can attacker-controlled identifiers reaching core/contracts/ClearinghouseStorage.sol / module-level logic collide in a shared mapping, bitmap, queue index, digest bucket, or derived storage key so that one user’s action overwrites, unlocks, or consumes another user’s state?

## Target
- File/function: core/contracts/ClearinghouseStorage.sol / module-level logic
- Entrypoint: User reaches ClearinghouseStorage-backed state through any deposit, withdrawal, liquidation, or settlement routed into Clearinghouse.
- Attacker controls: productId, engine routing, withdrawPool address assumptions, spreads encoding
- Exploit idea: Search for every derived storage key, bitmap slot, queue index, digest map, or hash bucket touched by core/contracts/ClearinghouseStorage.sol / module-level logic; then try to construct two economically different actions that land on the same storage location.
- Invariant to test: Distinct users, subaccounts, orders, withdrawals, products, and queue items must never alias the same live state slot unless they are intentionally the same object.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or loss of funds through state-key collision.
- Fast validation: Build a state-machine test that mutates product registration, spread-encoded products, and withdrawal routing assumptions through reachable user flows and assert storage-backed routing remains correct.

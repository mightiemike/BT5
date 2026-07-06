# Q153: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/ClearinghouseStorage.sol / module-level logic leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/ClearinghouseStorage.sol / module-level logic
- Entrypoint: User reaches ClearinghouseStorage-backed state through any deposit, withdrawal, liquidation, or settlement routed into Clearinghouse.
- Attacker controls: productId, engine routing, withdrawPool address assumptions, spreads encoding
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/ClearinghouseStorage.sol / module-level logic; then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Build a state-machine test that mutates product registration, spread-encoded products, and withdrawal routing assumptions through reachable user flows and assert storage-backed routing remains correct.

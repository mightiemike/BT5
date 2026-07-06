# Q325: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
- Entrypoint: User reaches PerpEngineState calculations through matching, liquidation, settlement, and health-check flows.
- Attacker controls: productId, amount, vQuoteBalance, funding index inputs, priceX18
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs); then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Fuzz balance states and funding deltas near zero, max leverage, and sign flips while comparing PerpEngineState outputs to a model implementation.

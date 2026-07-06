# Q2899: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/Endpoint.sol / upgradeEndpointTx(address _endpointTx) leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/Endpoint.sol / upgradeEndpointTx(address _endpointTx)
- Entrypoint: User waits for a signed batch that eventually reaches Endpoint.processTransaction(...) via the sequencer path.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/Endpoint.sol / upgradeEndpointTx(address _endpointTx); then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.

# Q2409: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner) leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner); then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

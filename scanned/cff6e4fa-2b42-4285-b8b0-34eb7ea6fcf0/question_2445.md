# Q2445: Spread or encoded-product aliasing

## Question
Can encoded spread state, composite product IDs, or product-bitmaps around core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner) alias to a different exposure than the health, pricing, or liquidation logic assumes, letting the attacker hide or reshape risk?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Fuzz every encoded spread leg, bitmap, and product-ID composition that reaches core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner), then compare the exposure seen by matching, health, settlement, and liquidation logic.
- Invariant to test: Queueing, replay protection, and signer linkage must not let a user mutate another account or reuse stale authorization.
- Expected HackenProof impact: Critical/High: logic attack causing hidden liabilities, wrong liquidation behavior, or unauthorized balance mutation through product aliasing.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

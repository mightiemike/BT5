# Q2319: Global accumulator bleed across users or products

## Question
Can attacker-controlled actions through core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner) push a shared accumulator such as fees, insurance, funding, utilization, queue counters, or collected balances in a way that later lets the attacker redeem, avoid, or shift value that should belong to another user or product?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Track every shared accumulator touched before and after core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner), then interleave two users or two products and see whether the second actor can benefit from state that the first actor should have exclusively paid for or earned.
- Invariant to test: Shared protocol accumulators must remain correctly partitioned by user, product, pool, and request semantics.
- Expected HackenProof impact: Critical/High: loss of funds or logic attack through value bleed across shared accounting buckets.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

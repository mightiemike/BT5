# Q2725: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, bytes memory signature, bool allowLinkedSigner) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, bytes memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, bytes memory signature, bool allowLinkedSigner), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

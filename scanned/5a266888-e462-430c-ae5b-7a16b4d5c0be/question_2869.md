# Q2869: Type confusion between signed intent and executed path

## Question
Can an attacker craft calldata or a signed payload so that core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, bytes memory signature, bool allowLinkedSigner) validates one semantic action but decodes or executes another semantic action with a different effect on balances, positions, recipients, or signers?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, bytes memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Cross-check the validated digest fields against the later decode/dispatch logic in core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, bytes memory signature, bool allowLinkedSigner), especially where transaction type, appendix bits, recipient, or derived subaccount state influence execution.
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation via action-type confusion.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

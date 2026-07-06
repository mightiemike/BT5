# Q2513: Failure-handling mismatch after IOffchainExchange.matchOrders(...)

## Question
Can attacker-controlled failure behavior around IOffchainExchange.matchOrders(...) leave core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, IEndpoint.CompactSignature memory signature, bool allowLinkedSigner) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignedTx(bytes32 sender, uint64 nonce, bytes calldata transaction, IEndpoint.CompactSignature memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Force IOffchainExchange.matchOrders(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: Queueing, replay protection, and signer linkage must not let a user mutate another account or reuse stale authorization.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

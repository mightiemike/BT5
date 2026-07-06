# Q977: Hotspot-driven review path

## Question
Does the implementation detail noted for core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction) create a reachable exploit path for an unprivileged attacker: MatchOrdersWithAmount introduces takerAmountDelta as a separate field from the signed orders.

## Target
- File/function: core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Translate the implementation note into an executable proof path and test whether the noted assumption breaks authorization, accounting, queue semantics, or settlement safety.
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

# Q322: Nonce consume mismatch across fail, cancel, or alternate path

## Question
Can the same nonce, idx, or fill marker around core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction) be left unused on one path but considered consumed on another, allowing replay on the favorable branch or grief-free reuse after partial execution?

## Target
- File/function: core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Exercise success, revert, partial-fill, cancel, and alternate-recipient branches around core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction); then compare whether replay protection is consumed consistently across all economically equivalent paths.
- Invariant to test: Replay protection must be consumed exactly once for each signed or queued instruction, regardless of which reachable execution branch is taken.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or transaction manipulation through inconsistent nonce consumption.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

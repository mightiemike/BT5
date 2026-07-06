# Q2281: Dust-cycle extraction or min-threshold bypass

## Question
Can repeated tiny user-controlled operations through core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner) stay below a per-step threshold, rounding guard, fee floor, or min-size rule while still accumulating a meaningful balance, position, or withdrawal advantage over many iterations?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Search for floor divisions, min-size exemptions, fee-on-first-fill logic, or first-deposit thresholds around core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner); then repeat the smallest admissible action until any measurable value leak or rule bypass appears.
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that extracts value by exploiting repeated micro-operations.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

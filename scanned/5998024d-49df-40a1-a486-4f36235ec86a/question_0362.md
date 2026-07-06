# Q362: Replay or cross-context reuse of sender

## Question
Can a signature or signed payload accepted by core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) be replayed in a different context where sender changes meaning, allowing the attacker to reuse valid authorization for a different economic effect?

## Target
- File/function: core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User or relayer submits fast-withdrawal signatures that WithdrawPool verifies through Verifier.requireValidTxSignatures(...).
- Attacker controls: transaction type, transaction body, sender, recipient, productId, amount, nonce, sendTo, appendix, idx
- Exploit idea: Try to replay the same signed bytes after mutating only the execution context for sender, including alternate product, queue, recipient, or isolated-subaccount conditions.
- Invariant to test: Batch or fast-withdrawal signatures must not be reusable across chain IDs, idx values, or semantically different transactions.
- Expected HackenProof impact: Critical/High: transaction manipulation or replay of signed orders, withdrawals, or liquidations.
- Fast validation: Build a withdrawal replay test that varies chain ID, idx, sendTo, and transaction bytes around requireValidTxSignatures(...).

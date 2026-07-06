# Q1306: Nonce consume mismatch across fail, cancel, or alternate path

## Question
Can the same nonce, idx, or fill marker around core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId) be left unused on one path but considered consumed on another, allowing replay on the favorable branch or grief-free reuse after partial execution?

## Target
- File/function: core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Exercise success, revert, partial-fill, cancel, and alternate-recipient branches around core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId); then compare whether replay protection is consumed consistently across all economically equivalent paths.
- Invariant to test: Replay protection must be consumed exactly once for each signed or queued instruction, regardless of which reachable execution branch is taken.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or transaction manipulation through inconsistent nonce consumption.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

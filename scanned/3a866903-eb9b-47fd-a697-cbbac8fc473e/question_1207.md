# Q1207: Dust-cycle extraction or min-threshold bypass

## Question
Can repeated tiny user-controlled operations through core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId) stay below a per-step threshold, rounding guard, fee floor, or min-size rule while still accumulating a meaningful balance, position, or withdrawal advantage over many iterations?

## Target
- File/function: core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Search for floor divisions, min-size exemptions, fee-on-first-fill logic, or first-deposit thresholds around core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId); then repeat the smallest admissible action until any measurable value leak or rule bypass appears.
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that extracts value by exploiting repeated micro-operations.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

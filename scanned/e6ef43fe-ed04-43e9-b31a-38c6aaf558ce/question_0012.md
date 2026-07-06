# Q12: Alternate encoding or packing gap

## Question
Can attacker-controlled calldata, struct packing, abi encoding, or byte slicing reaching core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) produce two byte representations that validate as the same intent in one stage but decode differently in another stage?

## Target
- File/function: core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Generate semantically similar but bytewise different payloads, packed structs, or appended bytes around core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker); then compare the digest, decode result, and executed side effects for any split-brain interpretation.
- Invariant to test: Encoding and decoding must be canonical enough that one authorized byte sequence cannot be reinterpreted as a different instruction downstream.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction type confusion through encoding mismatch.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

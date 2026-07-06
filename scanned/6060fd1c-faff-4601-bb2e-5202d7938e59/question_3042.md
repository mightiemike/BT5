# Q3042: Sender alias or linked-signer confusion

## Question
Can core/contracts/OffchainExchange.sol / tryCloseIsolatedSubaccount(bytes32 subaccount) treat msg.sender, signed sender, linked signer, fee owner, builder owner, or recipient-derived address as interchangeable in a way that lets one user spend or settle on behalf of another without fresh authorization?

## Target
- File/function: core/contracts/OffchainExchange.sol / tryCloseIsolatedSubaccount(bytes32 subaccount)
- Entrypoint: User later closes, claims, or settles through order-driven exchange flows that mutate OffchainExchange state.
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Hold the signed fields constant while varying caller, linked signer, isolated-subaccount mapping, builder ownership, and recipient-derived address interpretation to see whether core/contracts/OffchainExchange.sol / tryCloseIsolatedSubaccount(bytes32 subaccount) conflates those identities.
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation that mutates the wrong account context.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

# Q3969: Sender alias or linked-signer confusion

## Question
Can core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx) treat msg.sender, signed sender, linked signer, fee owner, builder owner, or recipient-derived address as interchangeable in a way that lets one user spend or settle on behalf of another without fresh authorization?

## Target
- File/function: core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Hold the signed fields constant while varying caller, linked signer, isolated-subaccount mapping, builder ownership, and recipient-derived address interpretation to see whether core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx) conflates those identities.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation that mutates the wrong account context.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.

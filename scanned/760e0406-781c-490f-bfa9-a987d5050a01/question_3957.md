# Q3957: Recipient routing or sendTo confusion

## Question
Can attacker-controlled recipient fields make core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx) pay the wrong recipient, let a linked signer redirect funds, or let a fast-withdrawal helper reinterpret sender-versus-sendTo semantics after signature verification?

## Target
- File/function: core/contracts/Clearinghouse.sol / withdrawCollateral(bytes32 sender, uint32 productId, uint128 amount, address sendTo, uint64 idx)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Vary sendTo, sender-derived default recipients, V2 appendix fields, and fee-payer branches to see whether the authorized withdrawal destination can be changed without a new valid authorization.
- Invariant to test: Withdrawals must route funds only to the intended recipient derived from the exact authorized withdrawal semantics.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal or transaction manipulation that reroutes funds.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.

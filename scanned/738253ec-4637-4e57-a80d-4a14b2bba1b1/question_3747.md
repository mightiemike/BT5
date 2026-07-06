# Q3747: Partial batch progress without full rollback

## Question
Can a loop, queue, or multi-step batch around core/contracts/Clearinghouse.sol / transferQuote(IEndpoint.TransferQuote calldata txn) make economic progress on early items even though a later item fails, leaving fill state, claim state, fees, or balances inconsistent with an all-or-nothing user assumption?

## Target
- File/function: core/contracts/Clearinghouse.sol / transferQuote(IEndpoint.TransferQuote calldata txn)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Construct a mixed-validity batch or queue sequence through core/contracts/Clearinghouse.sol / transferQuote(IEndpoint.TransferQuote calldata txn), force one later element to fail, and compare whether earlier state changes remain committed in a way that can be exploited or replayed.
- Invariant to test: Batched or queued user actions must either preserve consistent partial-progress rules or prevent attackers from extracting value from early-commit and late-fail combinations.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through inconsistent partial progress handling.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.

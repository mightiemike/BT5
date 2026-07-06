# Q554: Temporary solvency window across sequential updates

## Question
Can core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) apply a sequence of balance, funding, fee, or health updates in an order that lets the attacker briefly appear solvent and extract value before the final liability is applied?

## Target
- File/function: core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
- Entrypoint: User reaches PerpEngineState calculations through matching, liquidation, settlement, and health-check flows.
- Attacker controls: productId, amount, vQuoteBalance, funding index inputs, priceX18
- Exploit idea: Search for sequences where realized credits are applied before liabilities, funding, borrow costs, or fee debits around core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs); then attempt withdraw, transfer, or match operations inside that intermediate window.
- Invariant to test: A user must never be able to spend, withdraw, or avoid liquidation using equity that exists only during an intermediate update order.
- Expected HackenProof impact: Critical/High: logic attack causing unauthorized withdrawal, liquidation bypass, or system bad debt.
- Fast validation: Fuzz balance states and funding deltas near zero, max leverage, and sign flips while comparing PerpEngineState outputs to a model implementation.

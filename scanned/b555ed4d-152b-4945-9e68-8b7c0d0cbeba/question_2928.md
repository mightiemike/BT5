# Q2928: Beneficiary routing default or zero-value coercion

## Question
Can core/contracts/Clearinghouse.sol / depositInsurance(bytes calldata transaction) fall back to a default recipient, default subaccount, zero address, or caller-derived beneficiary in a way that lets the attacker redirect value or settle against the wrong destination without explicitly authorizing it?

## Target
- File/function: core/contracts/Clearinghouse.sol / depositInsurance(bytes calldata transaction)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Force optional recipient fields, empty sendTo values, zero subaccounts, unset isolated mappings, or caller-derived defaults around core/contracts/Clearinghouse.sol / depositInsurance(bytes calldata transaction) and compare who ultimately receives value or state updates.
- Invariant to test: Every value-moving action must resolve to exactly one intended beneficiary and must not silently substitute a different account or recipient.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, transfer, or account mutation through beneficiary confusion.
- Fast validation: Fuzz product IDs, decimals, health states, and sendTo values around Clearinghouse entrypoints and assert post-state solvency and ownership invariants.

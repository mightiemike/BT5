# Q1677: Temporary solvency window across sequential updates

## Question
Can core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount) apply a sequence of balance, funding, fee, or health updates in an order that lets the attacker briefly appear solvent and extract value before the final liability is applied?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount)
- Entrypoint: User submits a signed liquidation transaction that EndpointTx routes into Clearinghouse.liquidateSubaccount(...), which delegatecalls ClearinghouseLiq.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Search for sequences where realized credits are applied before liabilities, funding, borrow costs, or fee debits around core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount); then attempt withdraw, transfer, or match operations inside that intermediate window.
- Invariant to test: A user must never be able to spend, withdraw, or avoid liquidation using equity that exists only during an intermediate update order.
- Expected HackenProof impact: Critical/High: logic attack causing unauthorized withdrawal, liquidation bypass, or system bad debt.
- Fast validation: Fuzz quote balances, spread products, and product iteration order to test whether liquidation leaves insurance, balances, and open interest conserved.

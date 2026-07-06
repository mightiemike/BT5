# Q656: Arithmetic edge case in withdrawFeeX18

## Question
Can attacker-controlled extremes of withdrawFeeX18 drive core/contracts/SpotEngine.sol / socializeSubaccount(bytes32 subaccount) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/SpotEngine.sol / socializeSubaccount(bytes32 subaccount)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Fuzz withdrawFeeX18 around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/SpotEngine.sol / socializeSubaccount(bytes32 subaccount) mutates balances and risk state.
- Invariant to test: Spot balances, borrow/deposit multipliers, and utilization checks must conserve value across deposits, withdrawals, fills, NLP, and liquidation.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Fuzz signed amounts, product IDs, and zero-crossing transitions around SpotEngine.updateBalance(...) and assert no unbacked credit appears.

# Q1704: Arithmetic edge case in insurance

## Question
Can attacker-controlled extremes of insurance drive core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount)
- Entrypoint: User submits a signed liquidation transaction that EndpointTx routes into Clearinghouse.liquidateSubaccount(...), which delegatecalls ClearinghouseLiq.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Fuzz insurance around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount) mutates balances and risk state.
- Invariant to test: Delegatecalled liquidation logic must remain storage-safe and synchronized with clearinghouse accounting.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Trace delegatecall storage writes in liquidation and assert no path mutates unrelated storage slots or skips required post-checks.

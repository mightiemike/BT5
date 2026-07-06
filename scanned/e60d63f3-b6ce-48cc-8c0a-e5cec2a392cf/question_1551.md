# Q1551: Rounding leak through amount

## Question
Can repeated user-controlled updates around amount make core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / isAboveInitial(bytes32 subaccount)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving amount; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Only liquidatable accounts should be liquidated, and liquidation must not seize more than allowed or manufacture insurance/funding value.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write a Hardhat scenario that sets up healthy and unhealthy accounts, then fuzz liquidation amounts, spread encodings, and settlement ordering to assert exact seize bounds.

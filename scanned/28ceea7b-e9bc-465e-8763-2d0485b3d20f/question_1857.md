# Q1857: Ordering dependency around positive/negative PnL settlement order

## Question
Can an attacker manipulate reachable call order so that core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount) observes positive/negative PnL settlement order in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Reorder the same user actions around positive/negative PnL settlement order, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Liquidation ordering across spreads, liabilities, and PnL settlement must not let a user escape bad debt or overcharge another account.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Fuzz quote balances, spread products, and product iteration order to test whether liquidation leaves insurance, balances, and open interest conserved.

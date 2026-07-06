# Q2054: Stale or double-applied productToEngine

## Question
Can attacker-controlled sequencing make core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount) consume stale productToEngine or apply the same productToEngine transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale productToEngine before all related state is finalized.
- Invariant to test: Liquidation ordering across spreads, liabilities, and PnL settlement must not let a user escape bad debt or overcharge another account.
- Expected HackenProof impact: Critical/High: logic attack creating bad debt or draining insurance through liquidation math or ordering.
- Fast validation: Fuzz quote balances, spread products, and product iteration order to test whether liquidation leaves insurance, balances, and open interest conserved.

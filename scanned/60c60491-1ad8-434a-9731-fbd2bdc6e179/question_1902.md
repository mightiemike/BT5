# Q1902: Pre-check versus post-effect mismatch

## Question
Can core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount) satisfy an authorization, health, limit, or utilization check before a later effect changes the underlying balance or risk inputs, leaving the final state outside the condition that was actually checked?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Locate every require/assert-style gate around core/contracts/ClearinghouseLiq.sol / isUnderInitial(bytes32 subaccount), then mutate the referenced balances, fees, or risk variables later in the same path and compare the checked pre-state to the committed post-state.
- Invariant to test: Safety checks must guard the final committed effect, not only an earlier intermediate state that becomes invalid before the transaction ends.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, liquidation bypass, or logic attack through check-effect mismatch.
- Fast validation: Fuzz quote balances, spread products, and product iteration order to test whether liquidation leaves insurance, balances, and open interest conserved.

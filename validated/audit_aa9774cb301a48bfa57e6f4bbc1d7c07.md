### Title
Perp Fee Accounting Corruption in `claimSequencerFees` Due to Wrong Target Account — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

In `Clearinghouse.claimSequencerFees`, the perp fee loop uses `X_ACCOUNT` instead of `FEES_ACCOUNT` as the target of the second `updateBalance` call. This mirrors the external report's root cause: an incorrect parameter causes amounts to be attributed to the wrong destination, corrupting accounting. The result is that all accumulated perp fees are permanently stranded in `FEES_ACCOUNT` and never credited to `X_ACCOUNT`.

---

### Finding Description

`claimSequencerFees` processes both spot and perp fee balances. The spot loop is correct:

```solidity
// Spot loop (correct)
spotEngine.updateBalance(spotIds[i], X_ACCOUNT, fees[i] + feeBalance.amount);
spotEngine.updateBalance(spotIds[i], FEES_ACCOUNT, -feeBalance.amount);
```

The perp loop, however, uses `X_ACCOUNT` in **both** calls:

```solidity
// Perp loop (buggy)
perpEngine.updateBalance(perpIds[i], X_ACCOUNT, feeBalance.amount, feeBalance.vQuoteBalance);
perpEngine.updateBalance(perpIds[i], X_ACCOUNT, -feeBalance.amount, -feeBalance.vQuoteBalance);
//                                   ^^^^^^^^^^ should be FEES_ACCOUNT
```

The second call should zero out `FEES_ACCOUNT`, but instead it cancels the first credit to `X_ACCOUNT`. Net effect per iteration:
- `X_ACCOUNT` perp balance delta: `+feeBalance.amount − feeBalance.amount = 0`
- `FEES_ACCOUNT` perp balance delta: `0` (never touched)

Every call to `claimSequencerFees` leaves perp fees stranded in `FEES_ACCOUNT` and credits `X_ACCOUNT` with nothing. [1](#0-0) 

---

### Impact Explanation

All perp trading fees that accumulate in `FEES_ACCOUNT` are permanently locked there. `X_ACCOUNT` (the protocol treasury / sequencer fee recipient) receives zero perp fee revenue. Over time, the divergence between the on-chain `FEES_ACCOUNT` balance and the expected `X_ACCOUNT` credit grows monotonically with perp trading volume. This is a concrete, measurable asset accounting corruption: the corrupted balance is `sum(feeBalance.amount)` across all perp products for every `claimSequencerFees` invocation.

---

### Likelihood Explanation

`claimSequencerFees` is called by the sequencer during normal protocol operation via `submitTransactionsChecked`. No adversarial action is required — the bug fires on every legitimate invocation. The sequencer does not need to be compromised; the code path is the standard fee-settlement routine. [2](#0-1) 

---

### Recommendation

Change the second `updateBalance` call in the perp loop from `X_ACCOUNT` to `FEES_ACCOUNT`, matching the pattern used in the spot loop:

```solidity
// Fix
perpEngine.updateBalance(
    perpIds[i],
    FEES_ACCOUNT,   // was X_ACCOUNT
    -feeBalance.amount,
    -feeBalance.vQuoteBalance
);
```

---

### Proof of Concept

1. Perp trading occurs; fees accumulate in `FEES_ACCOUNT` for product `perpIds[i]`.
2. Sequencer calls `claimSequencerFees` via `submitTransactionsChecked`.
3. First call: `perpEngine.updateBalance(perpIds[i], X_ACCOUNT, +F, +V)` — credits `X_ACCOUNT`.
4. Second call: `perpEngine.updateBalance(perpIds[i], X_ACCOUNT, -F, -V)` — immediately reverses the credit on `X_ACCOUNT`.
5. `FEES_ACCOUNT` balance is unchanged; `X_ACCOUNT` net delta is zero.
6. Repeat for every `claimSequencerFees` call: perp fees accumulate in `FEES_ACCOUNT` indefinitely, never reaching `X_ACCOUNT`. [1](#0-0)

### Citations

**File:** core/contracts/Clearinghouse.sol (L569-615)
```text
    function claimSequencerFees(int128[] calldata fees)
        external
        virtual
        onlyEndpoint
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        uint32[] memory spotIds = spotEngine.getProductIds();
        uint32[] memory perpIds = perpEngine.getProductIds();

        for (uint256 i = 0; i < spotIds.length; i++) {
            ISpotEngine.Balance memory feeBalance = spotEngine.getBalance(
                spotIds[i],
                FEES_ACCOUNT
            );
            spotEngine.updateBalance(
                spotIds[i],
                X_ACCOUNT,
                fees[i] + feeBalance.amount
            );
            spotEngine.updateBalance(
                spotIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount
            );
        }

        for (uint256 i = 0; i < perpIds.length; i++) {
            IPerpEngine.Balance memory feeBalance = perpEngine.getBalance(
                perpIds[i],
                FEES_ACCOUNT
            );
            perpEngine.updateBalance(
                perpIds[i],
                X_ACCOUNT,
                feeBalance.amount,
                feeBalance.vQuoteBalance
            );
            perpEngine.updateBalance(
                perpIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount,
                -feeBalance.vQuoteBalance
            );
        }
    }
```

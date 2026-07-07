### Title
NLP Lock Period Exceeds Slow-Mode Delay, Causing Silent Consumption of Censorship-Resistance BurnNlp Transactions — (`core/contracts/common/Constants.sol`, `core/contracts/Clearinghouse.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

The `NLP_LOCK_PERIOD` (4 days) is strictly greater than `SLOW_MODE_TX_DELAY` (3 days). A user who mints NLP tokens and then submits a slow-mode `BurnNlp` transaction as their censorship-resistance escape hatch will have that transaction silently consumed and deleted from the queue — because the NLP tokens are still locked when the slow-mode window opens. The user loses the slow-mode fee and must resubmit, paying again and waiting an additional 3 days.

---

### Finding Description

Two protocol-level time constants are defined in `Constants.sol`:

```solidity
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
uint64 constant NLP_LOCK_PERIOD    = 4 * 24 * 60 * 60; // 4 days
``` [1](#0-0) 

The slow-mode mechanism is the protocol's censorship-resistance escape hatch. A user submits a transaction via `submitSlowModeTransaction()`, which is timestamped with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY` (3 days). After 3 days, anyone can call `executeSlowModeTransaction()` to process it. [2](#0-1) 

When the slow-mode transaction executes, it calls `clearinghouse.burnNlp()`, which enforces:

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
``` [3](#0-2) 

NLP tokens minted at time T are locked until `T + NLP_LOCK_PERIOD` (T + 4 days), tracked via `handleNlpLockedBalance`: [4](#0-3) 

If a user submits a slow-mode `BurnNlp` at T=0, it becomes executable at T+3 days. But the NLP tokens are locked until T+4 days. The `burnNlp` call reverts with `ERR_UNLOCKED_NLP_INSUFFICIENT`. This revert is silently swallowed by the try/catch in `_executeSlowModeTransaction`:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch { ... }
``` [5](#0-4) 

Critically, the transaction is **deleted from the queue before the try/catch**:

```solidity
SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
delete slowModeTxs[_slowModeConfig.txUpTo++];
``` [6](#0-5) 

The slow-mode transaction is permanently consumed with no effect. The user's NLP tokens remain locked, and the user must pay another `SLOW_MODE_FEE` ($1) and wait another 3 days to resubmit.

---

### Impact Explanation

The slow-mode path is the **only** on-chain censorship-resistance mechanism for users. When a user mints NLP tokens and the sequencer begins censoring them within the first day, the user's only recourse — a slow-mode `BurnNlp` — will silently fail and be consumed. The user:

1. Loses the `SLOW_MODE_FEE` ($1) paid at submission time.
2. Has their NLP tokens remain inaccessible for an additional 3 days beyond the expected 4-day lock.
3. Must resubmit and pay the fee again, with no guarantee the sequencer will not continue censoring.

The invariant broken is: *a user can always use the slow-mode path to exit their NLP position after the lock period*. This invariant fails for any user who submits a slow-mode `BurnNlp` within the first day after minting, because `SLOW_MODE_TX_DELAY (3 days) < NLP_LOCK_PERIOD (4 days)`.

---

### Likelihood Explanation

Any user who mints NLP tokens and then submits a slow-mode `BurnNlp` within the first 24 hours after minting will trigger this. This is a realistic scenario for any user who:
- Mints NLP and then decides to exit quickly (e.g., due to market conditions)
- Is being censored by the sequencer and uses the slow-mode path as intended

No attacker cooperation is required. The user triggers the failure themselves by using the protocol's documented escape hatch.

---

### Recommendation

Ensure the slow-mode delay accounts for the NLP lock period. Either:

1. Increase `SLOW_MODE_TX_DELAY` to be at least `NLP_LOCK_PERIOD` (4 days), or
2. In `burnNlp` (or its slow-mode handler), skip the `ERR_UNLOCKED_NLP_INSUFFICIENT` check and instead defer execution — or refund the slow-mode fee and re-queue the transaction when the prerequisite is not yet met.

The minimal fix analogous to the external report's recommendation is to align the two constants:

```diff
- uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
+ uint64 constant SLOW_MODE_TX_DELAY = 4 * 24 * 60 * 60; // 4 days (>= NLP_LOCK_PERIOD)
``` [1](#0-0) 

---

### Proof of Concept

1. User calls `depositCollateralWithReferral()` and then the sequencer processes a `MintNlp` transaction at block timestamp T=0. NLP tokens are locked until `T + 4 days`.
2. User calls `submitSlowModeTransaction()` with a `BurnNlp` payload at T=0. The transaction is queued with `executableAt = T + 3 days`.
3. At T+3 days, anyone calls `executeSlowModeTransaction()`.
4. Inside `_executeSlowModeTransaction`, the transaction is deleted from the queue: `delete slowModeTxs[_slowModeConfig.txUpTo++]`.
5. `processSlowModeTransaction` → `burnNlp` → `getNlpUnlockedBalance` returns 0 (tokens still locked until T+4 days) → reverts with `ERR_UNLOCKED_NLP_INSUFFICIENT`.
6. The revert is caught silently. The transaction is gone. The user's NLP balance is unchanged.
7. User must pay another `SLOW_MODE_FEE` and resubmit, waiting until T+6 days for the next executable window — 2 days after the NLP tokens were already unlocked. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/common/Constants.sol (L50-52)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days

uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
```

**File:** core/contracts/Clearinghouse.sol (L496-501)
```text
        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```

**File:** core/contracts/SpotEngine.sol (L162-165)
```text
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
```

**File:** core/contracts/Endpoint.sol (L185-229)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
    }
```

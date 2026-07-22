### Title
Retroactive Decay Rate Application in `OracleValueStopLossExtension` Silently Zeroes All Bin Watermarks on Rate Update, Bypassing the Stop-Loss Guard — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` stores a single per-pool `decayPerSecondE8` in `oracleStopLossConfig[pool_]`. Every time `_checkAndUpdateWatermarks` runs it applies that **current** rate to the full elapsed time `dt = block.timestamp − hwmS.lastDecayTs`. When the pool admin executes a decay-rate increase via `executeOracleStopLossDecay`, the new rate is retroactively applied to the entire dormant period since the last swap, not just from the moment of the change. For any pool that has been idle long enough, this collapses every bin's watermark to zero in the very next swap, permanently disabling the stop-loss guard for that swap and all subsequent ones until watermarks are manually re-raised.

---

### Finding Description

`_afterSwapOracleStopLoss` reads the pool-wide decay rate and passes it to `_checkAndUpdateWatermarks`:

```solidity
// OracleValueStopLossExtension.sol  lines 215-241
PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
uint256 decayRate = cfg.decayPerSecondE8;
...
_checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1,
                          floorMultiplier, decayRate, zeroForOne);
```

Inside `_checkAndUpdateWatermarks` the elapsed time is computed against the stored `lastDecayTs`:

```solidity
// lines 267-284
BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
uint256 dt = block.timestamp - hwmS.lastDecayTs;
(uint256 hwm0, bool breach0) =
    _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
```

`_decayed` returns 0 whenever `ratePerSecondE8 * dt >= 1e8`:

```solidity
// lines 319-324
uint256 factor = ratePerSecondE8 * dt;
if (factor >= E8) return 0;
return hwm - (hwm * factor) / E8;
```

`_applyWatermark` treats a zero watermark as "no breach" because `metric >= 0` is always true:

```solidity
// lines 333-335
if (metric >= hwm) return (metric, false);   // hwm == 0 → always passes
```

The decay rate is updated atomically by `executeOracleStopLossDecay` without touching any bin's `lastDecayTs`:

```solidity
// lines 139-147
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // global write, no per-bin reset
    ...
}
```

This is the exact structural analog of the ERC20Vesting `setTgeDate` bug: a single global field (`decayPerSecondE8` ↔ `tgeStartDate`) is updated and then applied retroactively to all existing per-bin records (`BinHighWatermarks` ↔ vesting schedules), instead of being cached at the time each record was last written.

---

### Impact Explanation

Once the watermarks collapse to zero the stop-loss invariant stated in the NatDoc — *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)"* — is broken for the entire pool. Any swap that would have been reverted by `OracleStopLossTriggered` now executes freely. LPs suffer direct principal loss through toxic-flow swaps that the guard was deployed to prevent. The loss is unbounded because the guard remains disabled until the pool admin manually re-proposes and re-executes high-watermark values through the full timelock cycle.

---

### Likelihood Explanation

The trigger is the pool admin (semi-trusted per the protocol's own trust model) executing a legitimate, timelocked admin action. No exploit contract or external attacker is required. The condition `ratePerSecondE8 * dt >= 1e8` is easy to satisfy:

- A rate of `1` (1e-8 per second) zeroes any watermark after `dt ≥ 1e8 s ≈ 3.2 years`.
- A rate of `100` zeroes after `dt ≥ 1e6 s ≈ 11.6 days`.
- A rate of `1e8` (100 %/s, the maximum) zeroes after `dt ≥ 1 s`.

Any pool that experiences a quiet period between the last swap and the rate-change execution is vulnerable. The timelock window (during which LPs could exit) does not prevent the retroactive collapse — it only delays the moment the new rate is written; the collapse happens on the very next swap after execution.

---

### Recommendation

Cache the decay rate inside each `BinHighWatermarks` record so that the rate used for a given interval is always the rate that was active when `lastDecayTs` was last written. Concretely:

1. Add a `uint32 decayRateE8` field to `BinHighWatermarks`.
2. In `_checkAndUpdateWatermarks`, decay using `hwmS.decayRateE8` (the cached rate), then write the current global rate into `hwmS.decayRateE8` alongside the updated `lastDecayTs`.
3. This mirrors the recommended fix for the ERC20Vesting bug: store the reference value per-record rather than reading
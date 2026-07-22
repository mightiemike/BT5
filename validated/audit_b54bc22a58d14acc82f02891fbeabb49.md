### Title
`lastDecayTs` Reset on Every Swap Regardless of Watermark Change Permanently Blocks Stop-Loss Recovery - (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._checkAndUpdateWatermarks` unconditionally writes `hwmS.lastDecayTs = uint32(block.timestamp)` on every swap that touches a bin, even when the watermark value did not change. An unprivileged attacker can exploit this by repeatedly executing tiny swaps in the non-blocked direction to keep the decay clock perpetually fresh, preventing the watermark from ever decaying and permanently locking the pool in a stop-loss-triggered state.

---

### Finding Description

The `OracleValueStopLossExtension` tracks per-bin value-per-share metrics against decaying high watermarks. When a metric falls below the drawdown floor, swaps in the offending direction revert. The intended recovery mechanism is that the watermark decays linearly at `decayPerSecondE8` per second, so after sufficient time the floor drops below the current metric and swaps are re-enabled.

The decay is computed lazily in `_checkAndUpdateWatermarks`:

```solidity
uint256 dt = block.timestamp - hwmS.lastDecayTs;
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
...
hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
hwmS.lastDecayTs = uint32(block.timestamp);   // ← always written
``` [1](#0-0) 

`_applyWatermark` returns the **old** watermark unchanged whenever `metric < hwm`:

```solidity
function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private pure returns (uint256 newHwm, bool breached)
{
    if (metric >= hwm) return (metric, false);   // ratchet up only here
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);                      // hwm unchanged
}
``` [2](#0-1) 

So when the metric is below the watermark but above the floor (within the drawdown band), the swap succeeds, the watermark value is unchanged, but `lastDecayTs` is reset to `block.timestamp`. On the very next call `dt = 0`, `_decayed()` returns the full watermark, and no decay has accumulated. [3](#0-2) 

This is structurally identical to the external report's `feesUpdatedAt` bug: a timestamp is updated even when no meaningful state transition occurred, erasing the time that should have been counted.

---

### Impact Explanation

When a stop-loss triggers and blocks direction A (e.g., `zeroForOne`), direction B (`oneForZero`) remains open. An attacker executes arbitrarily small swaps in direction B. Each swap calls `afterSwap` → `_checkAndUpdateWatermarks`, which resets `lastDecayTs` to `block.timestamp`. Because `dt` is always near zero on the next call, the watermark never decays. The floor (`hwm * floorMultiplier / E6`) never drops below the current metric, so direction A remains permanently blocked.

LPs cannot benefit from the pool recovering to normal two-way trading. The stop-loss protection, which is supposed to be temporary and self-healing via decay, becomes a permanent one-way lock. This constitutes broken core pool functionality with direct impact on LP assets (inability to exit via the blocked swap direction, and inability to earn fees from that direction).

---

### Likelihood Explanation

The trigger is fully unprivileged: any address can call a swap in the non-blocked direction. The cost is only gas for tiny swaps. The attacker does not need to hold LP shares or have any special role. The pool must have `decayPerSecondE8 > 0` and a stop-loss already triggered (one direction blocked), both of which are normal operational states for a pool using this extension.

---

### Recommendation

Only update `lastDecayTs` when the watermark actually ratchets up (i.e., when `metric >= hwm`). When the metric is below the watermark, the decay clock should continue running from its previous value so that accumulated time is not erased:

```solidity
// Only reset the decay clock when the watermark ratchets to a new high
if (hwm0 > hwmS.token0 || hwm1 > hwmS.token1) {
    hwmS.lastDecayTs = uint32(block.timestamp);
}
hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
// Do NOT unconditionally write lastDecayTs here
```

Alternatively, store the decayed watermark value (not the raw watermark) and update `lastDecayTs` only on ratchet-up, so that the decay already applied is not lost on the next touch.

---

### Proof of Concept

```
Setup:
  - Pool with OracleValueStopLossExtension, drawdownE6 = 50_000 (5%), decayPerSecondE8 = 58 (~5%/day)
  - Bin 0: t0=1000, t1=1000, shares=BIN_SHARES, price=Q64
  - First swap (oneForZero): afterSwap sets hwm0=hwm1=M, lastDecayTs=T0

Attack:
  T0: Value drops — t0=800, t1=800. metricT0 < hwm0 * 0.95 → breach0=true.
      zeroForOne swap reverts (OracleStopLossTriggered).

  T0+1s: Attacker calls a tiny oneForZero swap (direction B, not blocked).
         afterSwap → _checkAndUpdateWatermarks:
           dt = 1s, _decayed(hwm0, 58, 1) ≈ hwm0 * (1 - 58/1e8) ≈ hwm0 (negligible)
           metric < hwm → hwm0 unchanged, lastDecayTs = T0+1s

  T0+2s: Attacker repeats. lastDecayTs = T0+2s. dt=1s again. No net decay.

  ... (attacker repeats every block) ...

  T0 + 5 days: Without attack, watermark would have decayed ~25%, floor drops below metric,
               pool reopens. WITH attack: lastDecayTs is always block.timestamp-1s,
               effective decay ≈ 58/1e8 per second × 1s ≈ 0.000058% per call.
               Watermark is still effectively at M. Pool remains permanently blocked.
```

The attacker's cost is only gas for tiny swaps. The pool's stop-loss recovery is permanently defeated.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-284)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-324)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-336)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
  }
```

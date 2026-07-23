Looking at the actual code to trace the exact path.

### Title
Off-by-one in `_validateDrawdown` allows `drawdownE6 = 1e6`, zeroing `floorMultiplier` and permanently disabling stop-loss — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_validateDrawdown` uses a strict `>` comparison instead of `>=`, so `drawdownE6 = 1e6` (= `E6`) passes validation. Once executed, `floorMultiplier = E6 - drawdown = 0`, making the breach condition `metric < 0` — impossible for `uint256` — so `OracleStopLossTriggered` is never emitted and the stop-loss is silently disabled for all future swaps.

---

### Finding Description

**Validation off-by-one:** [1](#0-0) 

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

`drawdownE6 == E6 == 1e6` satisfies `drawdownE6 > E6 → false`, so no revert. The value is stored and later applied.

**Floor multiplier collapses to zero:** [2](#0-1) 

```solidity
uint256 floorMultiplier = E6 - drawdown;   // 1e6 - 1e6 = 0
```

**Breach check becomes `metric < 0` — always false for `uint256`:** [3](#0-2) 

```solidity
if (metric >= hwm) return (metric, false);
breached = metric < (hwm * floorMultiplier) / E6;  // metric < 0 → always false
return (hwm, breached);
```

The early-return guard at line 217 (`if (drawdown == 0) return;`) does **not** fire because `drawdown = 1e6 ≠ 0`, so the loop runs but never triggers a revert.

**Attack path:**

1. Pool admin calls `proposeOracleStopLossDrawdown(pool, 1e6)` — passes `_validateDrawdown`.
2. Waits for `timelock` seconds (zero if pool was initialized with `timelock = 0`).
3. Calls `executeOracleStopLossDrawdown(pool)` — writes `drawdownE6 = 1e6` to storage.
4. All subsequent swaps execute `_afterSwapOracleStopLoss` with `floorMultiplier = 0`; no breach is ever detected regardless of how much value is drained per bin.

The `onlyPoolAdmin` modifier resolves the admin from the factory registry — this is the pool creator, a semi-trusted role distinct from the factory owner. [4](#0-3) 

---

### Impact Explanation

The `OracleValueStopLossExtension` is the sole on-chain guard preventing per-bin value drain. With `floorMultiplier = 0` the guard is a no-op: any swap that moves value out of a bin — including a fully draining swap — completes without reverting. LPs who deposited under the assumption that the stop-loss extension protects their principal lose that protection entirely, with no on-chain recourse after the timelock elapses.

---

### Likelihood Explanation

- A pool admin who wishes to rug LPs has a direct, one-step path: propose `drawdownE6 = 1e6`, wait the timelock, execute.
- If the pool was initialized with `timelock = 0` (permitted by the initializer — no minimum timelock is enforced), the disable is atomic.
- Even with a non-zero timelock, LPs who are not actively monitoring `OracleStopLossDrawdownProposed` events have no automated protection.

---

### Recommendation

Change the strict inequality to `>=` in `_validateDrawdown`:

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 >= E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

This ensures `floorMultiplier` is always at least 1, keeping the breach check meaningful. Apply the same fix symmetrically to `_validateDecay` if a 100 % decay rate is also unintended.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_drawdown_1e6_disables_stop_loss() public {
    // pool admin proposes 100% drawdown
    vm.prank(poolAdmin);
    ext.proposeOracleStopLossDrawdown(pool, 1e6);

    // skip timelock (or 0 if initialized with timelock=0)
    skip(oracleStopLossConfig.timelock);

    vm.prank(poolAdmin);
    ext.executeOracleStopLossDrawdown(pool);

    // assert config
    assertEq(ext.oracleStopLossConfig(pool).drawdownE6, 1e6);

    // perform a fully draining swap — expect NO revert
    vm.expectEmit(false, false, false, false);
    // OracleStopLossTriggered is never emitted
    pool.swap(/* drain params */);
    // assertion: swap succeeded, stop-loss never fired
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L234-234)
```text
    uint256 floorMultiplier = E6 - drawdown;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-335)
```text
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
  }
```

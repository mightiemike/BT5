### Title
`PriceVelocityGuardExtension` velocity guard is silently bypassed on every pool's first swap due to uninitialized `lastMidPriceX64` — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap()` gates its entire velocity check behind `if (prevMid != 0)`. Because `lastMidPriceX64` starts at zero (uninitialized storage) and `PriceVelocityGuardExtension` has no factory-called `initialize` function, the guard is completely skipped on the first swap of every pool that uses it. Any oracle price — including a manipulated one — reaches the pool unchecked on that first call.

---

### Finding Description

`PriceVelocityGuardExtension` is a `beforeSwap` extension that caps how fast the oracle mid-price can move between blocks. Its core logic is:

```solidity
// PriceVelocityGuardExtension.sol lines 53-76
PriceVelocityState storage s = priceVelocityState[pool_];
uint128 prevMid = s.lastMidPriceX64;   // reads 0 on first swap
uint64 prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;          // state updated unconditionally
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                    // ← guard skipped when prevMid == 0
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;
        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);
        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);
        uint256 actualSq  = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
        if (actualSq > allowedSq) revert PriceVelocityExceeded(actualSq, allowedSq);
    }
}
```

`priceVelocityState[pool_].lastMidPriceX64` is a `uint128` mapping slot that defaults to `0`. Unlike `OracleValueStopLossExtension`, which has an `initialize(address pool, bytes calldata data)` function called by the factory at pool creation, `PriceVelocityGuardExtension` exposes only two admin setters — `setMaxChangePerBlock` and `setLastMidPrice` — both gated by `onlyPoolAdmin`. There is no factory-enforced initialization path that seeds `lastMidPriceX64` to a non-zero value before the first swap.

Consequently, on the first `swap()` call to any pool that has `PriceVelocityGuardExtension` configured as a `beforeSwap` extension:

1. `prevMid` reads `0`.
2. `s.lastMidPriceX64` is written to the live oracle mid-price.
3. The `if (prevMid != 0)` branch is not entered — **no velocity check runs**.
4. The hook returns `IMetricOmmExtensions.beforeSwap.selector` successfully.
5. The swap proceeds at whatever price the oracle currently reports, regardless of how far that price is from any reasonable baseline.

From the second swap onward, `prevMid` is non-zero and the guard operates normally.

---

### Impact Explanation

The velocity guard exists precisely to block bad-price execution when the oracle is manipulated (e.g., a Pyth price update pushed in the same block via a flash-loan-funded sandwich). On the first swap the guard is entirely absent. An attacker who can influence the oracle price in the same block as the first swap — a realistic scenario for Pyth-backed pools where anyone can push a signed price update — can execute a swap at an arbitrarily skewed mid-price. The pool's bin math settles at that price, draining LP token balances in the direction of the manipulated quote. This is a direct loss of LP principal with no on-chain protection in place.

**Impact: Medium** — bad-price execution reaches the pool on the first swap; LP funds are at risk proportional to pool depth and oracle skew achievable in one block.

---

### Likelihood Explanation

Every pool that registers `PriceVelocityGuardExtension` as a `beforeSwap` hook is affected on its very first swap. The pool admin is not prompted or required to call `setLastMidPrice` before opening the pool to users; the factory does not enforce it. The first-swap window is predictable (pool creation is public) and the trigger is fully unprivileged — any address can submit the first swap. On Pyth-backed pools the attacker can also push a fresh signed price update in the same transaction, making the oracle skew controllable.

**Likelihood: Medium** — the condition is always present at pool launch; exploitation requires timing the first swap with an oracle push, which is straightforward on Pyth.

---

### Recommendation

Add a factory-called `initialize` function to `PriceVelocityGuardExtension` (mirroring `OracleValueStopLossExtension`) that seeds `lastMidPriceX64` from the pool's live oracle price at pool creation time. Alternatively, treat `prevMid == 0` as a sentinel that **blocks** the swap (fail-closed) rather than skipping the check, forcing the pool admin to explicitly seed the price via `setLastMidPrice` before the first swap is permitted.

---

### Proof of Concept

1. Deploy a pool with `PriceVelocityGuardExtension` as a `beforeSwap` extension.
2. Pool admin calls `setMaxChangePerBlock(pool, 1e16)` (1 % per block cap). `lastMidPriceX64` remains `0`.
3. Attacker pushes a Pyth signed price update that moves the oracle mid-price 50 % from fair value in the same block.
4. Attacker calls `pool.swap(...)` — this is the first swap.
5. `beforeSwap` reads `prevMid = 0`, skips the velocity check, writes the manipulated mid-price to storage, and returns success.
6. The swap executes at the 50 %-skewed price; the pool pays out token1 (or token0) at the manipulated rate, draining LP value.
7. From the second swap onward, the 50 % jump from the now-stored manipulated baseline would itself be capped — but the damage from step 6 is already settled. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L24-34)
```text
  function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
  }

  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-76)
```text
    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
      }
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L46-68)
```text
  function initialize(address pool, bytes calldata data)
    external
    override(BaseMetricExtension, IOracleValueStopLossExtension)
    onlyFactory
    returns (bytes4)
  {
    if (oracleStopLossConfig[pool].initialized) {
      revert OracleStopLossAlreadyInitialized(pool);
    }

    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });

    emit OracleStopLossDrawdownSet(pool, drawdownE6);
    emit OracleStopLossDecaySet(pool, decayPerSecondE8);
    emit OracleStopLossTimelockSet(pool, timelock);
    return IMetricOmmExtensions.initialize.selector;
  }
```

**File:** metric-periphery/contracts/interfaces/extensions/IPriceVelocityGuardExtension.sol (L7-11)
```text
  struct PriceVelocityState {
    uint128 lastMidPriceX64;
    uint64 lastUpdateBlock;
    uint64 maxChangePerBlockE18;
  }
```

### Title
`PriceVelocityGuardExtension.setMaxChangePerBlock` accepts zero with no lower-bound check, allowing pool admin to freeze all swaps - (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary

`PriceVelocityGuardExtension.setMaxChangePerBlock` stores the caller-supplied `newMaxPctChangePerBlockE18` directly with no minimum or maximum validation. A pool admin (semi-trusted) can set this value to `0`, which causes the velocity guard's `beforeSwap` hook to revert on every subsequent swap whenever the oracle mid-price has moved even one unit since the last block, permanently freezing the pool's swap functionality.

### Finding Description

`setMaxChangePerBlock` writes the raw `uint64` argument into storage without any bounds check:

```solidity
function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
}
``` [1](#0-0) 

The extension's `beforeSwap` hook compares squared values to avoid an on-chain `sqrt`, as documented in the contract header:

```
changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
``` [2](#0-1) 

When `maxChangePerBlockE18 = 0`, the right-hand side collapses to `0 * (1 + blockDiff) = 0`. The check then becomes `changeE18^2 <= 0`, which fails for any non-zero oracle price movement. Because oracle prices update continuously in production, every swap after the first will revert once `prevMid` is populated.

The `beforeSwap` hook reads `prevMid` from storage and only skips the check when `prevMid == 0` (i.e., before the very first swap):

```solidity
uint128 prevMid = s.lastMidPriceX64;
...
s.lastMidPriceX64 = midPrice;
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {
    // velocity check — reverts if changeE18^2 > maxChangePerBlockE18^2 * (1 + blockDiff)
}
``` [3](#0-2) 

After the first swap sets `prevMid`, every subsequent swap with any oracle price movement will revert. The pool admin can also call `setLastMidPrice` to an arbitrary value, manufacturing a large apparent velocity jump that immediately triggers the guard even at a non-zero `maxChangePerBlockE18`.

```solidity
function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
}
``` [4](#0-3) 

By contrast, the `OracleValueStopLossExtension` validates its analogous parameters (`drawdownE6`, `decayPerSecondE8`) through explicit `_validateDrawdown` / `_validateDecay` calls at both initialization and every subsequent proposal, preventing zero or out-of-range values from being stored. [5](#0-4) 

No equivalent guard exists for `setMaxChangePerBlock`.

### Impact Explanation

Setting `maxChangePerBlockE18 = 0` renders the pool's swap path permanently unusable whenever the oracle price moves (the normal operating condition). Traders cannot execute swaps; LPs cannot earn fees; the pool is effectively bricked without a formal pause. This satisfies the allowed impact gate: **broken core pool functionality causing unusable swap flows**.

### Likelihood Explanation

Low. Requires a malicious or compromised pool admin — the same threat model as the seeding external report (malicious/compromised owner). The pool admin role is semi-trusted and is the direct analog to the `LoanVault` owner in the external bug.

### Recommendation

Add a minimum bound check in `setMaxChangePerBlock`. For example:

```solidity
uint64 internal constant MIN_MAX_CHANGE_PER_BLOCK_E18 = 1e15; // 0.1% per block minimum

function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    if (newMaxPctChangePerBlockE18 < MIN_MAX_CHANGE_PER_BLOCK_E18) revert MaxChangePerBlockTooLow();
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
}
```

Similarly, `setLastMidPrice` should validate that `newLastMidPriceX64` is within a reasonable range of the current oracle price to prevent manufactured velocity spikes.

### Proof of Concept

1. Pool is deployed with `PriceVelocityGuardExtension` as one of its seven extension hooks.
2. Pool admin calls `setMaxChangePerBlock(pool, 0)`.
3. A user executes the first swap — succeeds because `prevMid == 0`, and `lastMidPriceX64` is set to the current oracle mid.
4. The oracle price updates by any amount (normal operation).
5. A second user attempts a swap. `beforeSwap` is called: `prevMid != 0`, `changeE18 > 0`, check `changeE18^2 <= 0` fails → revert.
6. All subsequent swaps revert for as long as the oracle price differs from the value stored at step 3. The pool is frozen. [1](#0-0) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L15-18)
```text
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
///      where 1e18 = 100% (full unit).
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L24-27)
```text
  function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-34)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-60)
```text
  function beforeSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L56-68)
```text
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

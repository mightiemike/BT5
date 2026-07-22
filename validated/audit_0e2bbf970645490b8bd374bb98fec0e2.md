### Title
`PriceVelocityGuardExtension` Uses `block.number` as a Time Proxy, Rendering the Velocity Guard Miscalibrated on L2 Chains — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` measures oracle price velocity using `block.number` as its time unit. On Arbitrum, `block.number` returns the most recently synced L1 block number (updated once per minute), so all swaps within the same minute observe `blockDiff = 0`. This collapses the allowed price change to the bare minimum (`maxChangePerBlockE18`), causing the guard to revert every legitimate swap whenever the oracle price moves more than that threshold within a single minute — a routine occurrence for any volatile asset. On Optimism, the opposite distortion applies: every transaction is its own block, so `blockDiff` is always ≥ 1 and the guard is effectively per-transaction rather than per-time-unit, making it trivially permissive.

---

### Finding Description

`PriceVelocityGuardExtension.beforeSwap` records and compares oracle mid-prices using `block.number` as the elapsed-time proxy: [1](#0-0) 

```solidity
uint128 prevMid = s.lastMidPriceX64;
uint64 prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;
s.lastUpdateBlock = uint64(block.number);   // ← block.number used as clock

if (prevMid != 0) {
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;   // ← delta is in "blocks"
```

The allowed deviation formula is: [2](#0-1) 

```solidity
uint256 actualSq  = changeE18 * changeE18;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
if (actualSq > allowedSq) {
    revert PriceVelocityExceeded(actualSq, allowedSq);
}
```

The same `block.number` write appears in the admin setter: [3](#0-2) 

The codebase explicitly targets L2 deployment — `PriceProviderL2` and `ProtectedPriceProviderL2` both carry L2-specific staleness logic with a `FUTURE_TOLERANCE` parameter for sequencer clock skew: [4](#0-3) 

**Arbitrum behavior**: `block.number` returns the most recently synced L1 block number, updated approximately once per minute (~5 L1 blocks). All swaps that land within the same sync window share the same `block.number`, so `blockDiff = 0`. The allowed squared change collapses to `maxChange^2 * 1`, i.e., the single-block minimum. Any oracle price movement exceeding `maxChangePerBlockE18` within that minute causes every subsequent swap to revert with `PriceVelocityExceeded`.

**Optimism behavior**: Every transaction is its own block, so `blockDiff` is always ≥ 1 between consecutive swaps. The guard is calibrated per-transaction rather than per-time-unit, making it far more permissive than the admin intended.

---

### Impact Explanation

On Arbitrum, when the oracle price moves more than `maxChangePerBlockE18` within a single sync window (~60 seconds), the `beforeSwap` hook reverts for every swap direction until the next sync window resets `block.number`. This renders the pool's swap functionality completely unusable during normal market volatility — a broken core pool flow. LPs cannot rebalance through swaps and traders cannot execute. The guard, intended as a safety mechanism, becomes the source of the outage.

---

### Likelihood Explanation

Any pool deployed on Arbitrum or Optimism with `PriceVelocityGuardExtension` configured is affected. The trigger on Arbitrum requires only that the oracle price moves more than `maxChangePerBlockE18` within ~60 seconds, which is routine for any volatile asset pair. No attacker action is required; normal market conditions are sufficient. The presence of dedicated L2 price provider contracts (`PriceProviderL2`, `ProtectedPriceProviderL2`) confirms L2 deployment is an intended and supported use case.

---

### Recommendation

Replace `block.number` with `block.timestamp` throughout `PriceVelocityGuardExtension` and rename the configuration parameter from `maxChangePerBlockE18` to `maxChangePerSecondE18` (or a similar time-based unit). Update `PriceVelocityState.lastUpdateBlock` to `lastUpdateTimestamp` (a `uint64` storing seconds). Rewrite the allowed-deviation formula to use elapsed seconds:

```solidity
uint256 elapsedSeconds = block.timestamp - prevTimestamp;
uint256 allowedSq = uint256(maxChangePerSecond) * uint256(maxChangePerSecond) * (1 + elapsedSeconds);
```

This produces consistent, chain-agnostic behavior on Arbitrum, Optimism, and Ethereum mainnet.

---

### Proof of Concept

1. Deploy a pool on Arbitrum with `PriceVelocityGuardExtension` and `maxChangePerBlockE18 = 5e15` (0.5% per block, a reasonable Ethereum mainnet setting).
2. At time T, a swap executes. `lastUpdateBlock` is set to the current Arbitrum `block.number` (e.g., L1 block 20,000,000).
3. Within the next 55 seconds (before the next L1 sync), the oracle price moves 0.6% due to normal market activity.
4. A second swap is submitted. `block.number` is still 20,000,000 → `blockDiff = 0`.
5. `allowedSq = (5e15)^2 * (1 + 0) = 25e30`. `actualSq = (6e15)^2 = 36e30`. `36e30 > 25e30` → `PriceVelocityExceeded` reverts.
6. Every swap reverts until the next L1 block sync (~60 seconds later), during which the pool is completely non-functional. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L9-18)
```text
/// @title PriceVelocityGuardExtension
/// @notice Caps how fast the provided price can move between blocks, per pool.
/// @dev This extension allows the pool admin to increase security of the pool by limiting price
///      manipulation through velocity constraints. However, it assumes that the pool admin is not
///      an adversary and acts to optimize pool profitability. The pool admin must be trusted.
///
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
///      where 1e18 = 100% (full unit).
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-33)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L55-63)
```text
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L69-73)
```text
        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L36-38)
```text
    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;
```

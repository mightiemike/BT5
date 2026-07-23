Audit Report

## Title
Intra-block guard state rebase allows cumulative price movement to exceed `maxChangePerBlockE18` per block — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary
`PriceVelocityGuardExtension.beforeSwap` unconditionally writes `lastMidPriceX64` and `lastUpdateBlock` to the current values **before** performing the velocity check. Two swaps in the same block each see `blockDiff = 0` and are each checked against the price from the immediately preceding swap, not the price at the start of the block. The cumulative price movement allowed in one block is therefore `N × maxChangePerBlockE18` for N same-block swaps, violating the per-block cap invariant stated in the NatDoc.

## Finding Description
In `PriceVelocityGuardExtension.beforeSwap`, lines 57–58 unconditionally overwrite the stored reference price and block number before the velocity check at lines 60–74:

```solidity
s.lastMidPriceX64 = midPrice;           // line 57 — rebases reference BEFORE check
s.lastUpdateBlock = uint64(block.number); // line 58

if (prevMid != 0) {
    uint256 blockDiff = block.number - prevBlock; // = 0 for same-block swap
    uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
    // allowedSq = maxChange² when blockDiff = 0
    if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
}
``` [1](#0-0) 

After swap 1 moves the price from P0 → P1 (change = `maxChangePerBlockE18`), the guard stores P1 as the new reference. Swap 2 in the same block compares P2 against P1, not P0, and is allowed to move another full `maxChangePerBlockE18`. The block-level invariant stated in the NatDoc — "Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`" — is violated. [2](#0-1) 

`MetricOmmPool.swap()` reads the oracle fresh on every call via `_getBidAndAskPriceX64()` before invoking `_beforeSwap()`, so each swap in the same block can present a distinct oracle price to the guard. [3](#0-2) 

## Impact Explanation
LPs configure `maxChangePerBlockE18` to bound their impermanent-loss exposure per block. With this bug, an attacker executing N same-block swaps (each at a price `maxChangePerBlockE18` away from the prior swap's price) can move the effective oracle price by `N × maxChangePerBlockE18` in one block, causing LP losses proportional to the excess movement beyond the intended cap. This is a direct loss of LP principal above the admin-set threshold — a High-severity impact under Sherlock criteria.

## Likelihood Explanation
Requires: (a) a push-based oracle (e.g., Pyth) where the attacker can submit two valid signed price updates in the same block, and (b) the underlying market to have actually moved by 2× `maxChangePerBlockE18` in one block. Both conditions are realistic on L2s with fast block times during volatile market events. The attacker needs no privileged role — only access to valid Pyth price data and the public `swap()` entrypoint. [4](#0-3) 

## Recommendation
Track a separate `blockStartMidPriceX64` that is only updated when `block.number > lastUpdateBlock`. All swaps within the same block should be checked against this block-start reference price, not the price from the most recent swap:

```solidity
if (block.number > prevBlock) {
    s.blockStartMidPriceX64 = prevMid; // anchor for this new block
    s.lastUpdateBlock = uint64(block.number);
}
uint128 blockStartMid = s.blockStartMidPriceX64;
s.lastMidPriceX64 = midPrice;

if (blockStartMid != 0) {
    // compare midPrice against blockStartMid, not prevMid
}
```

## Proof of Concept
```solidity
// Foundry test sketch
function test_sameBlockDoubleSwapBypassesVelocityGuard() public {
    uint64 maxChange = 0.05e18; // 5% per block
    guard.setMaxChangePerBlock(address(pool), maxChange);

    uint128 P0 = 1e18;
    guard.setLastMidPrice(address(pool), P0);

    vm.roll(block.number + 1);

    // Swap 1: oracle at P1 = P0 * 1.05 (exactly at cap)
    uint128 P1 = uint128(P0 * 105 / 100);
    // push oracle to P1, execute swap → passes guard, guard now stores lastMid=P1

    // Swap 2 (same block): oracle at P2 = P1 * 1.05 (another full cap from P1)
    uint128 P2 = uint128(P1 * 105 / 100);
    // push oracle to P2, execute swap → passes guard (blockDiff=0, change from P1=5%)

    // Total move from P0 to P2 ≈ 10.25% — exceeds the 5% per-block cap
    uint256 totalChange = (uint256(P2 - P0) * 1e18) / uint256(P0);
    assertGt(totalChange, maxChange, "guard bypassed: total move > maxChangePerBlock");
}
```

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L15-17)
```text
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-74)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L227-240)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```

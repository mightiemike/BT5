### Title
Per-block velocity cap in `PriceVelocityGuardExtension` is per-swap, not per-block — multiple swaps in the same block compound the allowed movement beyond the configured envelope - (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` updates `lastMidPriceX64` on **every swap call**, so the velocity check measures the change between consecutive swaps rather than the change from the block-start price. When `blockDiff = 0`, each swap is individually allowed to move the price by `maxChangePerBlockE18`. N swaps in the same block therefore compound to `(1 + maxChange)^N` total movement — far exceeding the configured per-block cap.

---

### Finding Description

In `beforeSwap`, lines 57–58 unconditionally overwrite the stored checkpoint before the velocity check runs:

```solidity
s.lastMidPriceX64 = midPrice;          // checkpoint updated on every swap
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;   // 0 for same-block swaps
        ...
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
        // blockDiff=0 → allowedSq = maxChange^2 × 1
        if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
    }
}
``` [1](#0-0) 

When two swaps land in the same block (`blockDiff = 0`), the second swap compares the current mid price against the **first swap's** mid price — not the block-start price. Each swap independently receives a full `maxChangePerBlockE18` budget. The guard's NatSpec says it "caps how fast the provided price can move **between blocks**," but the implementation caps movement **between consecutive swaps**. [2](#0-1) 

The `MetricOmmSimpleRouter` exposes a public `multicall` that executes multiple `exactInputSingle` calls in a single transaction, and tests confirm two sequential swaps on the same pool in one transaction are fully supported: [3](#0-2) [4](#0-3) 

For the oracle price to differ between swaps in the same block, the oracle must be updated between them. The `PythOracle` accepts signed Lazer payloads via a public `fallback` — anyone holding a valid signed payload (i.e., any authorized Pyth Lazer publisher) can push a new price in the same transaction. The `CompressedOracle` additionally has a permissionless push path via `allowPushers`/`fallback` for its own namespace. [5](#0-4) 

An attacker with oracle write access constructs a single transaction:

```
oracle.push(P0 → P1)   // within maxChange of P0
pool.swap(...)          // guard: |P1-P0|/P0 ≤ maxChange ✓ → lastMid = P1
oracle.push(P1 → P2)   // within maxChange of P1
pool.swap(...)          // guard: |P2-P1|/P1 ≤ maxChange ✓ → lastMid = P2
...repeat N times...
```

After N steps the effective price is `P0 × (1 + maxChange)^N`, while the guard was supposed to limit the block's total movement to `P0 × (1 + maxChange)`.

---

### Impact Explanation

LPs suffer direct loss of principal. The pool is an oracle-anchored market maker: swap math uses the live oracle bid/ask. If the oracle price is driven far below true market value in a single block, the attacker buys token0 at a deeply discounted price, extracting value from LP reserves. The `PriceVelocityGuardExtension` is the only on-chain mechanism intended to bound this movement; bypassing it removes the sole protection against intra-block oracle manipulation.

---

### Likelihood Explanation

Medium. The attacker must be an authorized Pyth Lazer publisher (or control a `CompressedOracle` namespace that the pool's price provider reads). This is a semi-trusted role — not the pool admin or factory owner — and the `PriceVelocityGuardExtension` is explicitly designed to protect against oracle manipulation. The `MetricOmmSimpleRouter.multicall` and the public oracle push path are both permissionless entry points once oracle write access is obtained.

---

### Recommendation

Track the block-start mid price separately. Only advance `lastMidPriceX64` when `block.number > lastUpdateBlock`. Within the same block, always compare against the price that was current at the **start** of the block:

```solidity
// Only update the checkpoint when entering a new block
if (block.number > prevBlock) {
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
}
// Velocity check always uses the block-start price (prevMid)
```

This ensures the total price movement within any single block is bounded by `maxChangePerBlockE18`, regardless of how many swaps occur.

---

### Proof of Concept

Setup: pool with `PriceVelocityGuardExtension`, `maxChangePerBlockE18 = 0.05e18` (5% per block). Oracle initial mid price `P0 = 1000`.

```
Block N:
  Step 1: oracle.push(feedId, price=1050)   // +5% from 1000
  Step 2: pool.swap(zeroForOne=true, ...)
          → beforeSwap: prevMid=1000, midPrice=1050
            changeE18 = 50e18/1000 = 0.05e18
            actualSq  = (0.05e18)^2 = 2.5e33
            allowedSq = (0.05e18)^2 × (1+0) = 2.5e33  ✓ passes
            lastMidPriceX64 = 1050

  Step 3: oracle.push(feedId, price=1102)   // +5% from 1050
  Step 4: pool.swap(zeroForOne=true, ...)
          → beforeSwap: prevMid=1050, midPrice=1102
            changeE18 ≈ 0.05e18  ✓ passes
            lastMidPriceX64 = 1102

  ... repeat 10 times ...

  Final price ≈ 1000 × (1.05)^10 ≈ 1629  (+62.9% in one block)
  Intended cap: 1000 × 1.05 = 1050        (+5% in one block)
```

The attacker executes all oracle pushes and swaps atomically via a custom contract (or via `MetricOmmSimpleRouter.multicall` interleaved with oracle push calls), draining LP token0 reserves at a price 62.9% below true market value while the guard never reverts. [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-75)
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
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.t.sol (L822-854)
```text
  function test_twoSequentialSwapsSameTx() public {
    vm.startPrank(swapper);
    router.exactInputSingle(
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 500,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    router.exactInputSingle(
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 500,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    vm.stopPrank();
    _assertRouterEmpty();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** smart-contracts-poc/contracts/oracles/providers/PythOracle.sol (L36-60)
```text
    /// calldata format: [feedsLength:2][feedIds:feedsLength×4][priceUpdate:rest]
    /// No deadline prefix: every feed in the payload carries its own SIGNED
    /// FeedUpdateTimestamp — replay is neutralized by the per-feed monotonicity check.
    fallback() payable external override {
        uint256 end;

        assembly ("memory-safe") {
            end := calldatasize()
        }

        uint256 feedsLength;
        assembly ("memory-safe") {
            feedsLength := shr(240, calldataload(0)) // first 2 bytes
        }

        uint32[] memory updateFeedIds = new uint32[](feedsLength);
        assembly ("memory-safe") {
            let dst := add(updateFeedIds, 32)  // skip length slot
            let src := 2                       // offset after feedsLength(2)

            for { let i := 0 } lt(i, feedsLength) { i := add(i, 1) } {
                // load 32 bytes, shift right to get uint32 from high bits
                mstore(dst, shr(224, calldataload(src)))
                dst := add(dst, 32)
                src := add(src, 4)
```

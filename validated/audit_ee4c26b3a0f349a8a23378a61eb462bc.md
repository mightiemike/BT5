### Title
`PriceVelocityGuardExtension.beforeSwap` Missing `onlyPool` Modifier Allows Unprivileged State Manipulation to Bypass Velocity Guard — (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` overrides `BaseMetricExtension.beforeSwap` without re-applying the `onlyPool` modifier. Solidity does not propagate modifiers through overrides. Any external address can call the function directly with arbitrary bid/ask prices, overwriting `lastMidPriceX64` and `lastUpdateBlock` for any pool. This resets the velocity-guard baseline so the next real `pool.swap()` sees a near-zero price delta and passes the velocity check regardless of how far the oracle price has actually moved.

---

### Finding Description

`BaseMetricExtension` declares the base hook as `virtual onlyPool`: [1](#0-0) 

`PriceVelocityGuardExtension` overrides it with only `external override`, silently dropping the guard: [2](#0-1) 

The function writes to persistent storage **before** performing the velocity check:

```solidity
s.lastMidPriceX64 = midPrice;        // ← state written unconditionally
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                   // ← check comes after
    ...
    if (actualSq > allowedSq) {
        revert PriceVelocityExceeded(actualSq, allowedSq);
    }
}
``` [3](#0-2) 

Because there is no `onlyPool` check, any EOA or contract can call `PriceVelocityGuardExtension.beforeSwap` directly, supplying arbitrary `bidPriceX64` / `askPriceX64`. The geometric mid derived from those values is stored as the new `lastMidPriceX64`, and `lastUpdateBlock` is set to the current block. On the subsequent real `pool.swap()`, `prevMid` equals the attacker-injected value (≈ current oracle price), so `delta ≈ 0` and `actualSq ≤ allowedSq` regardless of how far the oracle price has actually moved since the last legitimate swap.

Contrast with `OracleValueStopLossExtension.afterSwap`, which explicitly compensates for the missing modifier with `_requireInitialized(msg.sender)` — a check that is only satisfiable by a factory-deployed pool: [4](#0-3) 

`PriceVelocityGuardExtension.beforeSwap` has no equivalent guard.

The pool's `swap()` entry point passes `msg.sender` as `sender` to `_beforeSwap`, which encodes it into the extension call: [5](#0-4) 

When the extension is called directly (not from the pool), `msg.sender` is the attacker — but the function never checks it.

---

### Impact Explanation

The velocity guard is the protocol's primary on-chain defense against rapid oracle price movement, including oracle manipulation. Bypassing it allows swaps to execute at prices that have moved beyond the configured per-block envelope. If the oracle price has been manipulated or has moved sharply, LP funds can be drained through unfavorable swaps that the guard was specifically designed to block. This is a direct loss of LP principal — matching the "bad-price execution" and "swap conservation failure" impact categories.

---

### Likelihood Explanation

The attack requires no special privileges, no tokens, and no complex setup. Any address can call `PriceVelocityGuardExtension.beforeSwap` directly. The pool and extension addresses are publicly discoverable from the factory. The state manipulation and the exploiting swap can be bundled into a single transaction or two consecutive transactions in the same block.

---

### Recommendation

Add the `onlyPool` modifier to `PriceVelocityGuardExtension.beforeSwap`, matching the base class intent:

```solidity
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
) external override onlyPool returns (bytes4) {   // ← add onlyPool
``` [6](#0-5) 

---

### Proof of Concept

```
// Setup
// - Pool deployed with PriceVelocityGuardExtension
// - maxChangePerBlockE18 = 1e16  (1 % per block cap)
// - lastMidPriceX64 = P_old  (set by a prior legitimate swap)
// - Oracle price has since moved to P_new = 1.05 * P_old  (5 % jump)
//   → a real swap would revert with PriceVelocityExceeded

// Step 1 — attacker resets the baseline (no tokens, no allowlist needed)
extension.beforeSwap(
    address(0),   // sender   — ignored by the function
    address(0),   // recipient — ignored
    false,        // zeroForOne — ignored
    0,            // amountSpecified — ignored
    0,            // priceLimitX64 — ignored
    0,            // packedSlot0Initial — ignored
    P_new - 1,    // bidPriceX64 ≈ current oracle price
    P_new + 1,    // askPriceX64 ≈ current oracle price
    ""
);
// Effect: lastMidPriceX64 = P_new, lastUpdateBlock = block.number

// Step 2 — attacker executes the real swap in the same block
// prevMid = P_new, midPrice from oracle = P_new → delta = 0
// actualSq = 0 ≤ allowedSq → velocity check passes
pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData);
// Swap executes at the price that should have been blocked
``` [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-79)
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

    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L199-204)
```text
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

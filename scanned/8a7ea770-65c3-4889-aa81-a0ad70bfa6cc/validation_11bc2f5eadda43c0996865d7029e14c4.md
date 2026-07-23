### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the ultimate user's address, allowing any user to bypass the per-pool swap allowlist by routing through `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is the production guard that restricts which addresses may execute swaps on a pool. Its `beforeSwap` hook checks `sender`, which is the `msg.sender` of the pool's `swap()` call. When a user routes through the public `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the user's. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those the admin explicitly excluded — can bypass the individual allowlist by calling the router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the immediate caller of `pool.swap`) is allowlisted for the pool (`msg.sender` inside the extension is the pool):

<cite repo="Oyahkilomeikhide/2026-07-metric-dev-oyakhil-main--024" path="metric-periphery/contracts/

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
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

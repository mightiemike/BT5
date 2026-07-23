### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool, which is always `msg.sender` to `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **actual swapper** is allowlisted. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for their curated users), the allowlist is completely bypassed for every user on the network.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → MetricOmmPool.swap(recipient, ...) [msg.sender = router]
     → _beforeSwap(msg.sender=router, recipient, ...)
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← wrong actor checked
```

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The pool requires the caller to implement `IMetricOmmSwapCallback`, so only the router (not the end user) can be `msg.sender` to the pool during a router-mediated swap. The actual user's address is never visible to the extension.

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the LP recipient — the economic actor), not `sender` (the direct pool caller): [4](#0-3) 

The swap allowlist and deposit allowlist are structurally inconsistent: the deposit guard correctly identifies the economic actor; the swap guard does not.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **every user on the network** can swap on the curated pool by routing through `MetricOmmSimpleRouter`, regardless of whether they are individually allowlisted. The allowlist provides zero protection against router-mediated swaps. This constitutes a complete bypass of a core pool access-control mechanism, allowing unauthorized users to trade on pools that were intended to be restricted (e.g., KYC-gated, institutional-only, or whitelist-only pools).

---

### Likelihood Explanation

The likelihood is medium-high. Any pool admin who:
1. Deploys a pool with `SwapAllowlistExtension` to restrict swaps to specific users, **and**
2. Wants those users to be able to use the standard `MetricOmmSimpleRouter` periphery

…must allowlist the router, which immediately opens the pool to all users. This is the natural and expected configuration for a curated pool that still supports the protocol's own router. The trap is invisible: the admin believes they are allowlisting a trusted intermediary, but they are actually disabling the allowlist entirely for all router-mediated swaps.

---

### Recommendation

The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Router passes user identity in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The `SwapAllowlistExtension` decodes and checks this value. This requires a trusted encoding convention and the extension to verify the payload source.

2. **Pool exposes an explicit `swapper` parameter**: Modify `MetricOmmPool.swap()` to accept an explicit `swapper` address (defaulting to `msg.sender` for direct calls), pass it through `_beforeSwap`, and have the extension check that address. The router would pass the actual user's address.

The simplest safe fix consistent with the existing `DepositAllowlistExtension` pattern is approach (1): the router encodes `abi.encode(msg.sender)` as the first word of `extensionData`, and `SwapAllowlistExtension.beforeSwap()` decodes and checks it when `extensionData` is non-empty, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended gated user)
  - allowedSwapper[pool][router] = true  (admin enables router support)

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Pool receives swap() from msg.sender=router
  - _beforeSwap(sender=router, ...) is called
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - Bob bypasses the curated allowlist entirely
  - The allowlist is rendered ineffective for all router-mediated swaps
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

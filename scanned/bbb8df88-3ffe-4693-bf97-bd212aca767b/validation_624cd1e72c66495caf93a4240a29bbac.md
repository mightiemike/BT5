### Title
`SwapAllowlistExtension` Checks Router Identity Instead of Original User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. Any pool admin who allowlists the router address (the only way to permit router-based swaps) simultaneously opens the gate to every user on the network, because the router is a public, permissionless contract.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When the swap originates from the router, `sender` is the router address, not the original user. The extension's NatSpec states it "Gates `swap` by swapper address, per pool," but the identity it actually gates is the direct caller of `pool.swap()`. [4](#0-3) 

The router calls `pool.swap()` directly with no mechanism to forward the original `msg.sender`: [5](#0-4) 

---

### Impact Explanation

Two concrete fund-impacting outcomes arise:

**1. Full allowlist bypass (High):** A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Because `MetricOmmSimpleRouter` is a public, permissionless contract, this single admin action opens the pool to every address on the network. Any non-allowlisted user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and the extension will pass, defeating the entire curation policy. The pool's LP assets are then exposed to unrestricted trading, which is exactly the risk the allowlist was deployed to prevent.

**2. Broken functionality for allowlisted users (Medium):** If the admin allowlists individual user addresses but not the router, those users cannot swap through the router even though they are explicitly permitted. They are forced to call `pool.swap()` directly, which requires implementing the `IMetricOmmSwapCallback` interface themselves. This breaks the expected user flow for curated pools.

---

### Likelihood Explanation

The likelihood is high. The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and shipped with the protocol. Any pool operator who deploys a curated pool with `SwapAllowlistExtension` and then tries to enable router access for their allowlisted users will trigger the bypass. The two outcomes (bypass or broken router access) are exhaustive: there is no configuration of the allowlist that correctly gates individual users while also permitting router-based swaps.

---

### Recommendation

The `beforeSwap` hook should gate on the economically relevant actor — the original user — not the intermediary router. Two approaches:

1. **Pass original user via `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`:** For single-hop swaps the recipient is often the original user, but this breaks for multi-hop flows where intermediate recipients are the router itself.

3. **Preferred — dedicated router field:** Add an `originator` field to the swap call path (analogous to how `addLiquidity` separates `sender` from `owner`) so the pool can forward the true initiating address to extensions without relying on `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) → msg.sender in pool = router
  - _beforeSwap passes sender=router to SwapAllowlistExtension
  - Extension checks allowedSwapper[pool][router] → true  ✓
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - The allowlist is completely bypassed for any user who routes through the router
  - LP assets in the curated pool are exposed to unrestricted trading
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

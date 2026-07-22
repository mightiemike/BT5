### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument forwarded by the pool, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the end-user. If the router is allowlisted (which is required for router-based swaps to function at all), every user who calls through the router bypasses the per-user allowlist entirely.

---

### Finding Description

**Call chain for a router swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender = router
             → _beforeSwap(msg.sender=router, recipient, ...)
                 → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     → checks allowedSwapper[pool][router]   ← WRONG ACTOR
```

The pool's `swap` function passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router never forwards the original caller's identity to the pool — it only stores it in transient storage for its own callback settlement: [4](#0-3) 

The actual end-user (`msg.sender` of `exactInputSingle`) is invisible to the extension.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses. To allow those users to trade via the standard router, the admin must allowlist the router address. Once the router is allowlisted, **any** address — including non-KYC'd users — can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it only sees the allowlisted router, not the individual caller. The curation policy is completely nullified for all router-mediated swaps. Users who should be blocked can drain liquidity from the pool at the oracle-anchored price, causing direct loss to LPs who relied on the allowlist for protection.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented and expected to be used by end-users. Any pool that (a) configures `SwapAllowlistExtension` and (b) allowlists the router — a necessary operational step — is immediately vulnerable. No special privileges, flash loans, or multi-step setup are required; a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **economic actor**, not the immediate pool caller. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is the economically relevant party. The pool already passes `recipient` as the second argument to `beforeSwap`; the extension can check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`.

The simplest correct fix for the extension:

```diff
- function beforeSwap(address sender, address, bool, ...)
+ function beforeSwap(address, address recipient, bool, ...)
    external view override returns (bytes4)
  {
-   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   (admin must do this for router to work)
  - allowedSwapper[pool][alice] = true
  - allowedSwapper[pool][bob]   = false   (bob is NOT allowlisted)

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...)  with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Bob's swap executes despite not being on the allowlist
``` [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

### Title
SwapAllowlistExtension Gates Router Address Instead of User Identity, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the originating user. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the allowlist by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — the router's identity — rather than the originating user's identity. A pool admin who wants to permit specific users to trade via the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through it, so **any** address can bypass the per-user gate by routing through `MetricOmmSimpleRouter`.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position owner explicitly supplied by the caller), not the `sender`: [5](#0-4) 

The swap extension has no equivalent owner-level field to fall back on; the only identity it receives is the direct caller of `pool.swap()`.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the pool and the extension will pass because it sees the allowlisted router address, not the blocked user. The pool's LP assets are exposed to unrestricted swaps, defeating the purpose of the curated pool and potentially causing direct loss of LP principal through adverse selection or price impact from actors the pool was designed to exclude.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is a routine, expected action: any pool admin who wants their allowlisted users to be able to use the standard periphery router must add the router to the allowlist. The moment they do, the gate is open to everyone. The attacker needs no special privilege — only the ability to call a public router function.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the **originating user** rather than the direct caller of `pool.swap()`. Two complementary approaches:

1. **Extend the extension interface**: Add an `originSender` field to `beforeSwap` (e.g., passed via `extensionData`) that the router populates with `msg.sender` before calling the pool. The extension then checks `allowedSwapper[pool][originSender]`.

2. **Check `sender` and fall back to `recipient`**: For router flows the recipient is often the user; however this is not reliable for multi-hop paths.

The cleanest fix is approach 1: the router encodes the originating user in `extensionData`, and the extension decodes and checks that address. The pool admin then allowlists individual users, not the router, and the router-mediated path is gated correctly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowedSwapper[pool][alice]  = true   (legitimate user)
  router → allowedSwapper[pool][router] = true   (admin adds router so alice can use it)

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)          // msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob despite bob not being allowlisted

Result:
  bob successfully swaps on a pool that was supposed to be restricted to alice only.
  LP assets are exposed to an unrestricted counterparty.
``` [6](#0-5) [7](#0-6) [1](#0-0)

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

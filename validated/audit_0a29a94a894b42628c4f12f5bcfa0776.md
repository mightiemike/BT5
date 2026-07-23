### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User: Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-based swaps, every unprivileged user can bypass the per-user allowlist entirely.

---

### Finding Description

**Root cause — pool passes `msg.sender` (the router) as `sender` to the extension:**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**The allowlist check uses that forwarded value:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — i.e., whoever called `pool.swap()`: [3](#0-2) 

**The router is always the direct caller of `pool.swap()`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops): [5](#0-4) [6](#0-5) 

**Consequence — two broken states, one of which is a full bypass:**

| Pool admin action | Result |
|---|---|
| Allowlists individual users, does NOT allowlist router | Allowlisted users cannot use the router at all; they must call `pool.swap()` directly. Router-based swap flow is broken for the intended audience. |
| Allowlists the router address (to enable router-based swaps) | `allowedSwapper[pool][router] == true` → every user, including non-allowlisted ones, passes the check. The per-user allowlist is completely bypassed. |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from using the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., a KYC-gated or private OTC pool) is fully open to any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted. Non-allowlisted users can execute swaps at oracle-derived prices, draining LP-owned token reserves that the pool admin intended to protect. This is a broken core swap-allowlist guard with direct LP-fund exposure.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap interface. Any pool that uses `SwapAllowlistExtension` and wants its allowlisted users to be able to use the router must allowlist the router — at which point the bypass is unconditional. The trigger requires no special privilege: any EOA calling `exactInputSingle` on the router suffices.

---

### Recommendation

The pool must pass the economically relevant actor — the end user — to the extension, not the intermediate router. Two complementary fixes:

1. **Pass the original user through the router:** Have the router forward the original `msg.sender` as a separate field in `extensionData`, and have the extension decode and check that field instead of the raw `sender` argument.

2. **Alternatively, gate on `recipient` instead of `sender`:** If the pool admin's intent is to restrict who *receives* output tokens, checking `recipient` (which the user controls and the router forwards unchanged) would correctly identify the end user. However, this changes the semantic of the allowlist.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension` decodes and checks it, falling back to `sender` when `extensionData` is empty (direct pool calls).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the intended gated user)
  allowedSwapper[pool][router] = true  (admin adds router so alice can use it)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, ...)   // msg.sender in pool = router

  pool calls:
    extension.beforeSwap(router, bob, ...)
    // msg.sender = pool, sender = router
    // checks: allowedSwapper[pool][router] == true  ✓
    // PASSES — bob's swap executes at oracle price

Result:
  bob swaps successfully despite not being on the allowlist.
  The per-user allowlist is completely ineffective for any router-mediated swap.
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

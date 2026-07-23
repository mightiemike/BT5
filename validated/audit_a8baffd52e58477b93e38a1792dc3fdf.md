### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed from the pool's `beforeSwap` hook. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract**, not the actual end user. If the router is allowlisted (so that legitimate users can use it), the allowlist is completely bypassed for every user. If the router is not allowlisted, no allowlisted user can use the standard periphery. Either way, the allowlist invariant is broken.

---

### Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` (or `exactInput` / `exactOutput`).
2. The router calls `pool.swap(params.recipient, ...)` — here `msg.sender` to the pool is the **router**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to the configured extension.
5. `SwapAllowlistExtension.beforeSwap(sender = router, ...)` checks `allowedSwapper[pool][router]`.

The extension never sees the actual end user.

**Relevant code:**

`MetricOmmPool.swap()` passes `msg.sender` (the router) as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, institutional partners). The allowlist is supposed to prevent unauthorized users from trading against LP funds.

**Bypass scenario:** The pool admin allowlists the router so that legitimate users can use the standard periphery. Because the extension checks the router address — not the actual caller — every user who calls the router can trade on the restricted pool, regardless of whether they are individually allowlisted. Non-allowlisted users can drain LP funds through adverse-selection trades that the pool admin explicitly intended to block.

**Lockout scenario:** If the pool admin does not allowlist the router, every allowlisted user is blocked from using the standard periphery and must call the pool directly, which requires implementing the swap callback interface — an unreasonable burden that effectively breaks the pool's usability.

In both cases the allowlist invariant is violated: the extension does not gate the economically relevant actor.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, documented periphery for all swaps. Any user who wants to bypass the allowlist simply calls the router instead of the pool directly — no special privileges, no malicious setup, no non-standard tokens required.
- The pool admin has no on-chain mechanism to distinguish router-mediated calls from direct calls at the extension level.
- The vulnerability is reachable on every pool that uses `SwapAllowlistExtension` with the router as the intended entry point.

---

### Recommendation

The extension must gate on the actual end user, not the intermediary. Two approaches:

1. **Pass the original caller through the router.** The router stores the real `msg.sender` in transient storage (it already does this for the payer). The pool could accept an explicit `originSender` parameter, or the extension could read it from a trusted router context. This requires a protocol-level change.

2. **Check `sender` against a router allowlist and separately verify the payer.** The extension could treat a known router as a transparent forwarder and require the router to attest the real user identity via `extensionData`. The extension would then verify the attested address against the allowlist.

The simplest safe interim fix: document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and revert in `beforeSwap` if `sender` is a known router address, forcing direct pool interaction only.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin allowlists only `alice` (allowedSwapper[pool][alice] = true).
  - Pool admin also allowlists the router so alice can use it
    (allowedSwapper[pool][router] = true).

Attack:
  - `bob` (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(...) with msg.sender = router.
  - Pool calls _beforeSwap(sender = router, ...).
  - Extension checks allowedSwapper[pool][router] → true → swap proceeds.
  - Bob successfully trades on the restricted pool.

Result:
  - Bob bypasses the allowlist entirely.
  - Every non-allowlisted user can do the same.
  - The pool admin's curation policy is completely ineffective.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on curated pools to a set of approved addresses. However, the extension checks the `sender` argument passed by the pool, which is `msg.sender` at the pool level — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (which is necessary for any allowlisted user to use the standard swap interface), the allowlist is nullified: any unprivileged user can bypass it by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that `sender` value and dispatches it to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` gates on that `sender` value.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Inside the extension, `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, substituting itself as `sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The dilemma the pool admin faces:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard router at all — broken UX |
| **Allowlist the router** | `allowedSwapper[pool][router] = true` → every user on the planet can call the router and pass the check |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users. The allowlist is structurally bypassed the moment the router is permitted.

---

### Impact Explanation

**Direct loss / broken core functionality — High.**

A curated pool (e.g., KYC-gated, institutional-only, or whitelist-restricted) deploys `SwapAllowlistExtension` to enforce access control. Once the router is allowlisted (the only way to let approved users use the standard interface), any unprivileged address can execute swaps against the pool by calling `MetricOmmSimpleRouter`. The allowlist protection is completely nullified, exposing the pool to unrestricted trading, potential price impact, and LP value leakage that the allowlist was specifically configured to prevent.

---

### Likelihood Explanation

**High.** The router is the canonical, documented swap interface for the protocol. Any pool admin who wants their allowlisted users to have a normal trading experience will allowlist the router. The bypass requires no special knowledge, no privileged access, and no unusual token behavior — any EOA can call `exactInputSingle` on the router.

---

### Recommendation

The extension must gate the **original user**, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, trusting only calls where `sender` is a known router and the decoded identity is allowlisted. This requires coordinated changes to the router and extension.

2. **Direct-pool-only allowlist policy:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that pair the extension with a router-allowlisted address). Allowlisted users must call `pool.swap()` directly.

Do **not** use `tx.origin` as a fix — it is unsafe in the presence of meta-transactions and account abstraction.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is the approved user
  admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (executed by bob, who is NOT allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: bob,
      ...
  })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives tokens

Result:
  bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist invariant is broken.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

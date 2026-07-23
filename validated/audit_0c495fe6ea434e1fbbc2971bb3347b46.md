### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool passes `msg.sender` (the router contract address) as `sender`, not the actual end user. If the router is allowlisted on the pool — the only way to make router-based swaps work on an allowlisted pool — every user, including non-allowlisted ones, can bypass the individual swap gate by routing through the router.

---

### Finding Description

**Root cause — pool passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  zeroForOne,
  amountSpecified,
  priceLimitX64,
  packedSlot0Initial,
  bidPriceX64,
  askPriceX64,
  extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` argument in the ABI-encoded call to every configured extension: [2](#0-1) 

**Guard checks the wrong identity:**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the router) and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`msg.sender` here is the pool (correct). `sender` is whatever the pool passed — the router address, not the actual end user.

**Router never forwards the real caller:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to embed the original `msg.sender` into the `sender` slot seen by extensions: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Broken invariant — two mutually exclusive failure modes:**

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | `allowedSwapper[pool][router] = true` → every user passes the guard regardless of individual allowlist status → **full bypass** |
| Router **is not** allowlisted | `allowedSwapper[pool][router] = false` → every router swap reverts → **allowlisted users cannot use the router** |

There is no configuration that simultaneously (a) lets allowlisted users swap through the router and (b) blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) and then allowlists the router to enable normal UX loses the allowlist entirely: any address can call `MetricOmmSimpleRouter.exactInputSingle` and execute a swap that the guard was supposed to block. Unauthorized users trade against restricted liquidity, violating the pool's access-control invariant and potentially causing direct loss to LPs who deposited under the assumption that only vetted counterparties could trade.

---

### Likelihood Explanation

The trigger is unprivileged: any user can call the public router. The precondition — a pool with `SwapAllowlistExtension` and the router allowlisted — is the natural, expected configuration for any operator who wants to restrict swaps while still supporting the standard periphery. The bypass requires no special role, no flash loan, and no exotic token behavior.

---

### Recommendation

The extension must receive the actual end-user address, not the intermediary contract address. Two viable approaches:

1. **Pass real caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; `SwapAllowlistExtension` decodes and checks it instead of (or in addition to) the `sender` argument. This requires a convention between router and extension.

2. **Check both `sender` and a decoded caller from `extensionData`**: The extension falls back to `sender` when no caller override is present (direct pool calls) and uses the decoded address when the router provides one.

Either way, the pool's `swap` interface should document that `sender` equals `msg.sender` of the pool call, which may be a router, so extension authors know they cannot rely on it for end-user identity.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
   → router calls pool.swap() with msg.sender = router
   → _beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes for Bob despite Bob not being on the allowlist
5. Alternatively, remove router from allowlist:
   → alice calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
   → extension checks allowedSwapper[pool][router] == false  ✗
   → NotAllowedToSwap() — alice cannot use the router at all
```

The `sender` identity mismatch is confirmed by the pool's `swap` implementation passing `msg.sender` directly: [6](#0-5) 

and the extension having no path to recover the real caller: [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

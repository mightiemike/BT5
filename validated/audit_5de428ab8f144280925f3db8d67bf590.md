### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, that `sender` is the router contract address, not the originating user. A pool admin who allowlists the router so that their permitted users can trade via the standard interface inadvertently opens the pool to every user, because any caller can reach the pool through the same router and inherit the router's allowlisted identity.

---

### Finding Description

`ExtensionCalling._beforeSwap` encodes `msg.sender` of the pool's `swap` call as the `sender` argument forwarded to every extension: [1](#0-0) 

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so when the router is the direct caller of the pool, `sender` = router address: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making itself `msg.sender` of the pool call: [4](#0-3) 

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard router interface |
| **Allowlist the router** | Every user on-chain can bypass the allowlist by calling the router |

There is no path that simultaneously allows permitted users to trade via the router and blocks unpermitted users from doing the same, because the extension sees only the router's address, never the originating EOA.

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` (e.g., KYC-gated, institutional, or regulatory-restricted) is fully bypassed by any unpermitted user who calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist guard silently fails open for every router-mediated swap once the router is allowlisted. This is an admin-boundary break: an unprivileged path (the public router) circumvents the access control the pool admin configured.

---

### Likelihood Explanation

The router is the canonical, documented user-facing entry point for swaps. Any pool admin who wants their allowlisted users to trade via the standard interface must allowlist the router. This is the expected operational configuration, making the bypass reachable in every realistic curated-pool deployment. No special permissions, flash loans, or unusual token behavior are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into the `extensionData` it forwards to the pool, and `SwapAllowlistExtension` decodes and checks that address instead of (or in addition to) `sender`.

2. **Check both `sender` and a decoded originator**: If `sender` is a known router, decode the real caller from `extensionData`; otherwise fall back to `sender`. This preserves backward compatibility for direct pool calls.

Either way, the allowlist mapping key must be the economic actor who controls the trade, not the contract that relays it.

---

### Proof of Concept

```
Setup:
  pool = new MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)      // alice is permitted
  admin calls setAllowedToSwap(pool, router, true)     // router allowlisted so alice can use it

Attack (bob is NOT allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...)          // msg.sender = router
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob with no restriction

Result:
  bob trades on a pool he was never permitted to access.
  The allowlist invariant is broken.
``` [3](#0-2) [5](#0-4)

### Citations

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

### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router to let allowlisted users trade through it, every unprivileged user gains the same access, defeating the curation policy entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← pool's direct caller, not the economic actor
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

So `sender` arriving at the extension is the **router address**, not the user. The extension has no access to the real user's identity; `extensionData` is user-controlled but the extension ignores it entirely.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (broken functionality) |
| **Allowlist the router** | Every user, including non-allowlisted ones, can bypass the restriction by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, market-maker-only, or institution-only) with `SwapAllowlistExtension` and then allowlists the router to support standard periphery usage inadvertently opens the pool to **all** users. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the pool without being individually allowlisted. This exposes LP positions to toxic flow from actors the pool was explicitly designed to exclude, causing direct LP principal loss.

**Impact: Medium** — LP value loss through unrestricted toxic flow on a pool designed to be curated.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point. Any allowlisted user who wants permit support, multi-hop routing, or deadline enforcement will naturally use the router, prompting the pool admin to allowlist it. The bypass is then immediately available to any unprivileged address with no special knowledge or capital required.

**Likelihood: Medium** — Triggered whenever the pool admin allowlists the router to support normal periphery usage.

---

### Recommendation

The extension should verify the **actual economic actor**, not the direct pool caller. Two viable approaches:

1. **Decode the real user from `extensionData`**: Require the router to encode `msg.sender` (the user) into `extensionData` and have the extension verify a signed or router-attested identity. This requires a coordinated change to the router.

2. **Check `sender` only when it is not a known router; otherwise decode user from `extensionData`**: The extension can maintain a registry of trusted routers and, when `sender` is a trusted router, extract and check the real user from `extensionData`.

3. **Gate on `sender` and document that the router must not be allowlisted**: Accept the limitation and document clearly that allowlisting the router opens the pool to all users, so pool admins must choose between router support and strict user-level curation.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only allowed swapper.
3. alice uses MetricOmmSimpleRouter.exactInputSingle → reverts (router not allowlisted).
4. Pool admin calls setAllowedToSwap(pool, router, true) to fix alice's access.
5. charlie (never allowlisted) calls MetricOmmSimpleRouter.exactInputSingle on the same pool.
   → pool.swap() is called with msg.sender = router
   → beforeSwap receives sender = router
   → allowedSwapper[pool][router] == true  ✓
   → swap succeeds — charlie bypasses the allowlist entirely.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

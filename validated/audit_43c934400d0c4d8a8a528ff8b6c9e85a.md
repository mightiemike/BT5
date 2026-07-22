### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the **direct caller of `pool.swap()`**. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router contract, not the original user. A pool admin who allowlists the router (the natural action to enable router-mediated swaps for their allowlisted users) inadvertently opens the pool to **every** user, completely defeating the access control.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the first argument forwarded to `_beforeSwap` is `msg.sender`: [1](#0-0) 

`_beforeSwap` encodes that value as the `sender` argument in the ABI call to the extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks that `sender` against the per-pool allowlist.**

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. [3](#0-2) 

**Step 3 — MetricOmmSimpleRouter calls `pool.swap()` directly, making itself the `sender`.**

In every entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap()` without forwarding the original `msg.sender`: [4](#0-3) [5](#0-4) 

The router's address — not the original user's address — is what the extension sees as `sender`.

**Step 4 — The allowlist becomes an all-or-nothing router gate.**

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Allowlist the router | Every user (including non-allowlisted ones) can swap via the router — allowlist fully bypassed |
| Do not allowlist the router | Allowlisted users cannot use the router at all — core swap path broken |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(router, ...)`. The extension checks `allowedSwapper[pool][router]` — which is `true` if the admin allowlisted the router. The swap executes. The allowlist is completely defeated. Unauthorized users can drain LP assets, execute swaps at oracle-derived prices the admin intended to restrict, and generate protocol fees from unauthorized activity. This is a direct loss of LP principal and a broken core pool invariant.

### Likelihood Explanation

The pool admin must allowlist the router to enable router-mediated swaps for their intended users. This is the natural and expected action — the admin has no reason to suspect that allowlisting the router opens the pool to everyone. The `SwapAllowlistExtension` documentation says it "gates `swap` by swapper address," which implies user-level gating, not router-level gating. The mismatch between expectation and implementation makes this a realistic misconfiguration.

### Recommendation

The `SwapAllowlistExtension` should gate on the **original user**, not the direct pool caller. Two options:

1. **Pass original caller via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is opt-in.
2. **Check `sender` only for direct calls; require router to forward user identity**: Add a standard mechanism (e.g., a `senderOverride` field in `extensionData`) that the extension trusts only when `msg.sender` (the pool's caller) is a known router, and falls back to `sender` for direct calls.

The simplest safe fix is to document that `SwapAllowlistExtension` only works correctly for **direct** `pool.swap()` calls and must not be used with the router, or to redesign the extension to decode the true originator from `extensionData`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)  →  msg.sender to pool = router
  - _beforeSwap(router, ...)  →  extension checks allowedSwapper[pool][router] = true
  - Swap executes for bob — allowlist bypassed

Invariant broken:
  allowedSwapper[pool][bob] == false, yet bob's swap settles successfully.
``` [3](#0-2) [1](#0-0) [6](#0-5)

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

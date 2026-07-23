### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address — not the actual user's address. If the router is allowlisted (the only way to let legitimate users use the router), every unpermissioned user can bypass the allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

The pool populates that `sender` argument with its own `msg.sender` — the direct caller of `pool.swap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value verbatim as the first positional argument to every configured extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router is the direct caller of `pool.swap`: [4](#0-3) 

So the extension sees `sender = address(router)`, not the actual end-user. The allowlist check becomes `allowedSwapper[pool][router]`.

There are two outcomes, both harmful:

1. **Router not allowlisted**: Every legitimate allowlisted user is blocked from using the router, breaking the primary public swap path.
2. **Router allowlisted** (the only fix for outcome 1): `allowedSwapper[pool][router] == true`, so the check passes for every caller regardless of their individual allowlist status. Any unpermissioned user can bypass the curated pool's access control by routing through the public router.

The analog to the Solana vault bug is exact: just as `deposit_token` was never verified against `allowed_token`, the actual swapper identity is never verified against the allowlist — the intermediary's identity is checked instead.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted counterparties loses that protection entirely once the router is allowlisted. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against LP liquidity. LP funds are exposed to trades from actors the pool admin explicitly intended to exclude. This is a direct loss-of-principal risk for LPs on curated pools and a complete failure of the pool's core access-control invariant.

---

### Likelihood Explanation

The router is the canonical, documented entry point for end-users. A pool admin who configures `SwapAllowlistExtension` and wants legitimate users to be able to use the router must allowlist the router address. This is the natural operational path. The bypass is therefore reachable in any realistic deployment where the router is permitted, which is the common case. No special privileges, flash loans, or exotic token behavior are required — a single public `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the economically relevant actor — the end-user — not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: ignore the `sender` argument (which is the direct pool caller) and instead require callers to supply the real user address in `extensionData`, or require direct pool calls only.

2. **Preferred — router-level forwarding**: `MetricOmmSimpleRouter` should forward the original `msg.sender` (the end-user) to the pool as a verified field in `extensionData`, and `SwapAllowlistExtension` should decode and check that field rather than the `sender` positional argument. The pool's `msg.sender` is always the router in this path and is not a useful identity for allowlist purposes.

A minimal patch to the extension:

```solidity
// Instead of checking `sender` (the router), decode the real user from extensionData
function beforeSwap(
    address,          // sender — ignored; this is the router, not the user
    address,
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata extensionData
) external view override returns (bytes4) {
    address realUser = abi.decode(extensionData, (address));
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][realUser]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The router must then encode `msg.sender` into `extensionData` before forwarding to the pool.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  admin calls setAllowedToSwap(pool, alice, true)      // alice is KYC'd
  admin calls setAllowedToSwap(pool, router, true)     // needed so alice can use the router
  // bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)           // msg.sender = router
    → pool calls _beforeSwap(router, ...)
    → ExtensionCalling encodes sender = router
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob receives output tokens from LP liquidity

Result:
  bob, an unpermissioned user, successfully swaps on a curated pool.
  The allowlist protecting LP funds is completely bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

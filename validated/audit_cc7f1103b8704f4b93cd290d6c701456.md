### Title
`SwapAllowlistExtension` gates the router address instead of the economic actor, allowing any user to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. If the pool admin allowlists the router (a necessary step to let any allowlisted user trade via the router), every unprivileged user can bypass the per-user allowlist by calling the router.

---

### Finding Description

**Hook dispatch — what `sender` actually is**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` field of the extension call: [2](#0-1) 

**What the allowlist checks**

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value above: [3](#0-2) 

**What the router passes**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The original user's address is stored only in the transient callback context (for payment), but is never forwarded to the pool as a parameter: [4](#0-3) 

The pool therefore receives `msg.sender = router`, and the extension sees `sender = router` for every router-mediated swap. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**The inescapable dilemma**

The pool admin faces two mutually exclusive outcomes:

| Admin action | Result |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot swap through the router at all — core swap flow is broken for them |
| **Does** allowlist the router | Every user, including non-allowlisted ones, can bypass the guard by calling `exactInputSingle` |

**Contrast with `DepositAllowlistExtension`**

The deposit allowlist correctly checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`: [5](#0-4) 

This asymmetry confirms the swap allowlist's design is inconsistent with how the router mediates calls.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified market makers, private LP strategies) is fully bypassed once the pool admin allowlists the router. Any unprivileged user can call `exactInputSingle` and trade against the pool's liquidity. Unauthorized swaps expose LP positions to adverse selection and value extraction that the allowlist was designed to prevent — a direct loss of LP principal.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router, which is the natural and expected action when the admin wants allowlisted users to be able to use the standard periphery. The admin has no way to simultaneously allow router-mediated swaps for approved users and block them for unapproved users, so the bypass is triggered by any reasonable deployment of the allowlist extension alongside the router.

---

### Recommendation

Pass the original user's address through the swap path so the extension can gate on the economic actor rather than the intermediary. Two complementary fixes:

1. **Extension-data convention**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with the pool as the trusted source of the outer `sender` for anti-spoofing).

2. **Dedicated sender field**: Add an explicit `originator` parameter to the pool's `swap` interface (set by the router to `msg.sender`, set by direct callers to `address(0)` or themselves) and forward it to extensions alongside `sender`.

Alternatively, mirror the deposit allowlist design: gate on a field that represents the economic actor (the address that will pay for the swap via callback), not the contract that called `swap`.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` as `extension1` and `BEFORE_SWAP_ORDER` pointing to it.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)`. The pool sets `sender = router` and calls `SwapAllowlistExtension.beforeSwap(router, ...)`.
6. The extension checks `allowedSwapper[pool][router]` → `true`. Bob's swap succeeds.
7. Bob has bypassed the allowlist and traded against the private pool's liquidity. [6](#0-5) [7](#0-6) [1](#0-0)

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

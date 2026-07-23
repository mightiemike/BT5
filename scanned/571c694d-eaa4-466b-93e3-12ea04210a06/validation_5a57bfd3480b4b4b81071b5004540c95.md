### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. If the pool admin allowlists the router (required for any router-mediated swap to succeed for allowlisted users), every user who routes through the router bypasses the individual-user allowlist entirely.

### Finding Description

The call chain for a router-mediated swap is:

1. User calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` — here `msg.sender` to the pool is the **router**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` = router address.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. [1](#0-0) 

The check is against the router's address, not the originating user. The pool admin faces an impossible choice:

- **Allowlist the router** → every user who calls through the router passes the check, making the allowlist meaningless.
- **Do not allowlist the router** → even allowlisted users cannot swap via the router; they must call the pool directly.

The pool's `swap` function passes `msg.sender` (the immediate caller) as `sender` to the extension: [2](#0-1) 

The router calls the pool directly without forwarding the original user identity: [3](#0-2) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses can be bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The user receives output tokens from the pool without being on the allowlist. This breaks the core curation invariant of the extension and constitutes a direct policy bypass on a live pool.

### Likelihood Explanation

Any pool admin who wants allowlisted users to be able to use the standard periphery router must allowlist the router contract. This is the natural operational setup. Once the router is allowlisted, the bypass is available to every user with no special privileges — they simply call `exactInputSingle` or `exactInput` on the router. The router is a deployed, public, immutable contract.

### Recommendation

The `beforeSwap` hook signature already receives both `sender` (immediate pool caller) and `recipient`. To gate the originating user, the extension needs the original user's address. Two approaches:

1. **Pass the original user in `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires trust that the router is the only entry point, which is not enforced.

2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers and, when `sender` is a router, falls back to checking the `recipient` or a user address embedded in `extensionData`.

3. **Preferred — enforce direct-pool-only swaps for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call the pool directly. Add a `onlyDirect` flag to the extension that reverts if `msg.sender` (the pool) was called by a known router.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-based swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(...) with msg.sender = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  - Swap executes. Attacker receives output tokens.
  - allowedSwapper[pool][attacker] was never set to true.
```

The attacker successfully swaps on a pool that was supposed to restrict trading to a curated set of addresses. [4](#0-3) [5](#0-4) [2](#0-1) [6](#0-5)

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

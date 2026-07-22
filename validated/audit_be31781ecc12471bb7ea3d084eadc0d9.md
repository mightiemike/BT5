### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When `MetricOmmSimpleRouter` is the immediate caller of `pool.swap()`, the pool passes `msg.sender` (the router) as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the pool to every user; a pool admin who does not allowlist the router silently breaks router access for every individually-allowlisted user.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender == msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly without forwarding the original `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
// msg.sender seen by pool == router address
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Bypass path:** Pool admin allowlists the router so that router-based swaps work → `allowedSwapper[pool][router] = true` → every user, including those the admin intended to block, can call `router.exactInputSingle(...)` and the extension passes unconditionally.

**Lockout path:** Pool admin does not allowlist the router → `allowedSwapper[pool][router] = false` → every individually-allowlisted user is silently blocked from using the router, even though they are permitted to trade.

There is no mechanism inside `SwapAllowlistExtension` to recover the original user identity from the router call; the router does not forward `msg.sender` in any calldata field that the extension can read.

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institutional-only, or regulatory-restricted) that deploys `SwapAllowlistExtension` to enforce per-user access control loses that protection entirely when the `MetricOmmSimpleRouter` is involved. Any unprivileged user can route a swap through the router and bypass the allowlist, trading in a pool they are not permitted to access. This constitutes a direct curation failure and an admin-boundary break: the pool admin's configured access policy is bypassed by an unprivileged public path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any user who discovers that the router is allowlisted (or simply tries routing) can exploit this immediately with no special privileges, no malicious setup, and no non-standard tokens. The condition is reachable on every swap through the router against a pool that has `SwapAllowlistExtension` configured.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the `sender` parameter only when the immediate caller is a trusted router that forwards the real user identity, or the extension should be redesigned to accept the real user address via `extensionData`. One concrete fix: have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that value when `sender` is a known router. Alternatively, the pool should pass the original user address as a dedicated field through the hook chain rather than reusing `msg.sender` of `pool.swap()`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `extension.setAllowedToSwap(pool, router, true)` to enable router-based swaps (or the admin allowlists the router believing it is the correct entry).
3. Attacker (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` = `true` → passes.
7. Swap executes; attacker trades in a pool they are not permitted to access.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

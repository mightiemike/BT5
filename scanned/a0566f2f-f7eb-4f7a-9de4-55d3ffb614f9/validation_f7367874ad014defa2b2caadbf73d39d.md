### Title
`SwapAllowlistExtension` checks router address instead of end-user, making the allowlist bypassable via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` (the immediate caller of `pool.swap`). When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router contract**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual trader is allowlisted. Any non-allowlisted user can bypass a curated pool's swap restriction by calling through the public router.

---

### Finding Description

**Call chain when routing through `MetricOmmSimpleRouter.exactInputSingle`:**

1. User calls `router.exactInputSingle(params)`.
2. Router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is the **router address**.
3. Pool calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`.
4. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates `allowedSwapper[pool][router]`.

The extension never sees the end user's address.

**`MetricOmmPool.swap` passes `msg.sender` as `sender`:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that value unchanged to every extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is whoever called `pool.swap` (the router):** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` the pool sees:** [4](#0-3) 

This creates an irresolvable dilemma for pool admins:

- **Router NOT allowlisted:** Allowlisted users cannot swap through the router (the router's address fails the check), forcing them to call the pool directly and implement `IMetricOmmSwapCallback` themselves.
- **Router IS allowlisted:** Every user on the network can bypass the allowlist by routing through the public router, defeating the curation entirely.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) can be bypassed by any unprivileged user via `MetricOmmSimpleRouter`. The bypassing user executes swaps at oracle-anchored prices against LP liquidity, extracting value from the pool in a manner the pool admin explicitly intended to prevent. This is a direct loss of LP principal and fee revenue to unauthorized traders, and constitutes broken core pool functionality (the allowlist guard fails open on the standard public swap path).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed for the protocol. Any pool admin who enables `SwapAllowlistExtension` and also wants users to be able to swap (even allowlisted ones) through the router must allowlist the router address, at which point the bypass is immediately available to all users. The trigger requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **end user's identity**, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the original user through the router:** Add a `swapper` field to the router's `extensionData` payload and have the extension decode and check it. This requires the extension to trust the router, which must be verified via `onlyPool` + a router registry.

2. **Check `sender` against a router registry and fall back to a user-supplied identity in `extensionData`:** If `sender` is a known trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

The simplest correct fix is to not allowlist the router at all and require direct pool calls for allowlisted users, but this breaks the standard UX. The proper fix is to thread the original `msg.sender` from the router into the extension context so the guard always evaluates the economic actor.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    });
  - Router calls pool.swap(attacker, true, X, ...).
  - Pool calls _beforeSwap(msg.sender=router, ...).
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  - Swap executes against LP liquidity at oracle price.
  - attacker receives output tokens; allowlist is never consulted for attacker's address.
```

The extension's `allowedSwapper[pool][attacker]` mapping is never read. The guard is fully bypassed. [5](#0-4) [6](#0-5)

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

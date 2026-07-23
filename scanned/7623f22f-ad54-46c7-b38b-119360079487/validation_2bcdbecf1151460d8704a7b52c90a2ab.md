### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every non-allowlisted user can bypass the per-user gate by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes the pool's `msg.sender`: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; their swaps revert |
| **Do** allowlist the router | Every non-allowlisted user can bypass the gate by routing through the same public router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's primary mechanism for restricting swap access to specific counterparties (e.g., KYC'd users, whitelisted market makers, or institutional LPs). Once the router is allowlisted — a necessary step for any allowlisted user to access the pool via the standard periphery — the gate is fully open to all users. Any non-allowlisted address can execute swaps of arbitrary size in the restricted pool, draining LP value at oracle-derived prices without the pool admin's consent. This is a broken core pool functionality with direct loss of LP assets above Sherlock thresholds.

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router, which is the expected operational step for any pool that wants allowlisted users to access the pool through the standard periphery. No privileged access, no special token behavior, and no malicious setup is required from the attacker — they simply call `exactInputSingle` or `exactInput` on the public router.

---

### Recommendation

The extension must gate the **end user**, not the immediate caller. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires the extension to trust only known routers as forwarders (checked via a factory-registered router allowlist).

2. **Recipient-based gating**: Gate on `recipient` instead of `sender`. The recipient is the address that receives output tokens and is set by the end user in the router call. This is harder to spoof and more closely represents the economic beneficiary. However, it would need careful analysis for multi-hop paths where intermediate recipients are the router itself.

The minimal safe fix is to add a `trustedForwarder` registry to `SwapAllowlistExtension`: if `sender` is a trusted forwarder, decode the real caller from `extensionData`; otherwise check `sender` directly.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, userA, true)      // legitimate user
  admin calls setAllowedToSwap(pool, router, true)     // required for userA to use router

Attack:
  userB (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool:       pool,
      recipient:  userB,
      zeroForOne: true,
      amountIn:   X,
      ...
    })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=userB, ...)          // pool.msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (gate passes)
      → tokens transferred to userB

Result:
  userB swaps successfully in a pool they are not allowlisted for.
  The SwapAllowlistExtension invariant is broken.
``` [5](#0-4) [6](#0-5) [1](#0-0)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

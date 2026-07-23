### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` inside `MetricOmmPool.swap()`. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every unprivileged user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap()` fires `_beforeSwap(msg.sender, recipient, ...)`, passing `msg.sender` — the direct caller of `pool.swap()` — as the `sender` argument to every configured extension. [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, with no forwarding of the originating user's address: [3](#0-2) 

So when a user enters through the router, the extension sees `sender = router`, not the user. This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (broken UX) |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the gate by calling the router |

The second option is the natural operational choice, and it silently opens the allowlist to the entire public.

The analog to the external report is exact: in `batchRelease`, state written in the first loop makes the second loop's reads return 0, so no tokens are transferred. Here, the identity written into `sender` by the pool (the router's address) makes the allowlist check read the wrong actor, so the guard never fires against the real user.

---

### Impact Explanation

Any user can swap on a pool that is configured with `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The allowlist — the sole mechanism for restricting who may trade on a curated pool — is rendered ineffective. Unauthorized users gain full swap access, which can result in direct loss of LP value through uninvited price impact, fee extraction, or MEV on pools that were designed to be restricted.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entry point. Pool admins who deploy `SwapAllowlistExtension` and want their allowlisted users to be able to use the router **must** allowlist the router address. This is the expected operational path. The bypass is therefore reachable on any production pool that uses the allowlist extension with router support enabled, with no special preconditions for the attacker beyond calling a public function.

---

### Recommendation

The router must forward the originating user's identity to the pool so the extension can gate the correct actor. Two complementary fixes:

1. **Add a `swapOnBehalf` parameter to `pool.swap()`** (or a separate entry point) that accepts the real initiator address, verified by the router before forwarding. The extension then checks this forwarded address instead of `sender`.

2. **Alternatively**, change `SwapAllowlistExtension.beforeSwap` to check the `recipient` or a caller-supplied identity field in `extensionData`, and have the router encode `msg.sender` there — with the extension verifying the encoding came from a trusted router.

Until fixed, pool admins should be warned that allowlisting the router grants unrestricted swap access to all users.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists alice (legitimate user) and the router (for usability).
// Bob is NOT allowlisted.

// Bob calls the router directly — sender seen by the extension is the router, not Bob.
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Extension checks: allowedSwapper[pool][router] == true  → swap succeeds
// Bob has bypassed the allowlist.

// Direct call by Bob (without router) would correctly revert:
pool.swap(bob, true, 1000e18, 0, "", "");
// Extension checks: allowedSwapper[pool][bob] == false → NotAllowedToSwap()
``` [2](#0-1) [4](#0-3) [1](#0-0)

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

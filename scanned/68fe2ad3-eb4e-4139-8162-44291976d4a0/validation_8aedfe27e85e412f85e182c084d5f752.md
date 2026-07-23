### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual end-user, allowing non-allowlisted users to bypass the swap gate via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router (a necessary step for legitimate users to use it), every non-allowlisted user can bypass the swap gate by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`msg.sender` inside the extension is the pool (enforced by `onlyPool`), so the pool must pass the caller of `pool.swap()` as `sender`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)` directly: [2](#0-1) 

At that point `msg.sender` of `pool.swap` is the router address, so the extension receives `sender = router`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Router **not** allowlisted | Legitimate allowlisted users cannot use the router; they must call the pool directly |
| Router **allowlisted** | Every non-allowlisted user bypasses the gate by routing through the public router |

The `DepositAllowlistExtension` does not share this flaw because it gates by `owner` (the LP-share beneficiary), which the liquidity adder passes through unchanged and which the caller cannot substitute for themselves without being on the allowlist: [3](#0-2) 

The swap extension has no equivalent binding to the economically relevant actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for access control (KYC-gated, institutional, or regulatory-restricted pools) loses its enforcement guarantee the moment the router is allowlisted. Any non-allowlisted address can trade in the restricted pool by calling `exactInputSingle` or `exactInput` on the public router. This breaks the core pool functionality the extension was deployed to provide and constitutes an admin-boundary break reachable by an unprivileged path.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for end-users. A pool admin who wants allowlisted users to have a normal UX must allowlist the router. The bypass is then trivially reachable by any address with no special privileges, no front-running, and no capital requirement beyond the swap input.

---

### Recommendation

The extension must gate on the actual end-user identity, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed payload.
2. **Check `recipient` instead of `sender`**: If the pool's invariant is "only allowlisted addresses may receive swap output," gate on the `recipient` argument, which the router passes as the caller-supplied `params.recipient` and cannot be spoofed to an arbitrary address without the recipient's cooperation.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin.setAllowedToSwap(pool, alice, true)      // Alice is allowlisted
  admin.setAllowedToSwap(pool, router, true)     // router allowlisted so Alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender of pool.swap = router

  pool calls:
    extension.beforeSwap(router, bob, ...)
    // extension checks allowedSwapper[pool][router] → TRUE
    // bob's swap succeeds despite not being on the allowlist
``` [4](#0-3) [5](#0-4)

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

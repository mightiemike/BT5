Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address. A pool admin who allowlists the router to support router-mediated swaps for permitted users inadvertently opens the gate to every user, completely defeating the allowlist's access control.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with its own `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — The extension keys the allowlist on that `sender`.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

The mapping is keyed `pool → swapper → bool`: [3](#0-2) 

**Step 3 — The router calls `pool.swap()` as itself, erasing the original user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The pool's `msg.sender` is therefore the router contract, not the end user: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (outer hop): [5](#0-4) 

**Step 4 — The dilemma that creates the bypass.**

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | `allowedSwapper[pool][router]` = true → **every user** passes the check by routing through the router |

Once the router address is inserted as the `swapper`, the check `allowedSwapper[pool][router]` is true for every caller of the router, regardless of who they are. The `extensionData` passed by the router is the user-supplied `params.extensionData` with no encoding of `msg.sender`, so there is no existing mechanism to recover the original user's identity inside the extension. [6](#0-5) 

## Impact Explanation

Any user who is explicitly **not** on the allowlist can bypass the swap gate on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool executes the swap, transfers output tokens to the user's chosen `recipient`, and pulls input tokens from the user via the swap callback — all without the allowlist ever seeing the real user's address. This constitutes a direct, fund-impacting policy bypass: the pool's curated access control is rendered ineffective, and disallowed counterparties can swap at oracle prices. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, documented periphery swap path. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router — this is the natural operational step. Once taken, the bypass is immediately available to every user with no further preconditions, no special tokens, and no privileged access. The attack is trivially repeatable.

## Recommendation

The extension must gate on the **original user's identity**, not the immediate caller of `pool.swap()`. The most robust fix is to gate on `recipient` (the second argument to `beforeSwap`) rather than `sender`. For a swap allowlist, the economically relevant actor is the recipient of output tokens, and the user always controls `recipient` regardless of router intermediation. Alternatively, the router can encode `msg.sender` into `extensionData` and the extension can decode and verify it, but this requires a trusted-router assumption.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is allowed
  allowedSwapper[pool][bob]   = false  // bob is blocked
  allowedSwapper[pool][router] = true  // admin allowlists router so alice can use it

Attack (bob):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls pool.swap(bob, true, X, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. Extension checks allowedSwapper[pool][router] == true → PASSES
  5. Swap executes; bob receives output tokens
  6. Bob has bypassed the allowlist entirely
``` [7](#0-6) [8](#0-7) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

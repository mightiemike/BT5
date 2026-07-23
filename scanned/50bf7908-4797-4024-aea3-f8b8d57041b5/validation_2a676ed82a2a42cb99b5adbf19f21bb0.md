### Title
SwapAllowlistExtension Gates Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool, which is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router (required for allowlisted users to use the router), every user — including non-allowlisted ones — can bypass the individual allowlist by routing through the router.

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // <-- this is the router when called via MetricOmmSimpleRouter
  recipient, ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is used, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

So `sender` received by the extension is the **router address**, not the original user (`msg.sender` of the router call). The original user's identity is never forwarded to the extension.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users (e.g., KYC'd addresses A, B, C).
2. Pool admin also allowlists the router address so that A, B, C can use the router for multi-hop or slippage-protected swaps — a normal operational requirement.
3. Non-allowlisted user D calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router]` → `true` (admin added it for A/B/C).
6. User D's swap executes successfully, bypassing the allowlist entirely.

The pool admin faces an impossible choice: either allowlist the router (opening the gate to everyone) or don't (breaking router access for legitimate allowlisted users).

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of `SwapAllowlistExtension`. Pools configured for restricted trading (regulatory compliance, whitelisted market makers, private pools) are fully open to any caller via the public router. Unauthorized swaps drain pool liquidity at oracle prices, directly harming LP principal.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard public swap interface. Any user who discovers the allowlist can trivially route around it. The pool admin enabling router access for legitimate users is the normal operational path, making the precondition (router allowlisted) highly likely in any real deployment.

### Recommendation

The extension must check the **original user**, not the immediate pool caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes it. This requires a convention between router and extension.

2. **Check `sender` only for direct pool calls; require the router to forward the original user as a verified field**: The router could be modified to pass the original user as the `recipient` or in a dedicated field, and the extension checks that field.

The cleanest fix is for `MetricOmmSimpleRouter` to encode the original `msg.sender` into `extensionData` and for `SwapAllowlistExtension` to decode and check it when the immediate `sender` is a known router, or for the pool to expose a mechanism for the router to attest the original caller.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (beforeSwapOrder = extension1)
  - allowedSwapper[pool][userA] = true
  - allowedSwapper[pool][router] = true  (admin adds this so userA can use router)

Attack:
  - userD (not allowlisted) calls:
      router.exactInputSingle({pool: pool, tokenIn: T0, tokenOut: T1, ...})
  - Router calls pool.swap(recipient=userD, ...) → msg.sender=router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for userD despite userD not being allowlisted

Result:
  - userD receives token output from a restricted pool
  - SwapAllowlistExtension invariant broken: non-allowlisted user traded
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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

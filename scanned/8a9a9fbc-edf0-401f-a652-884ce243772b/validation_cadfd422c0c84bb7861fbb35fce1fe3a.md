### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Non-Allowlisted Users to Bypass the Swap Guard via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the router contract, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps for their whitelisted users, every non-allowlisted user can also bypass the guard by routing through the same router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` inside `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ...
``` [3](#0-2) 

So `msg.sender` inside `pool.swap()` is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Attack path:**

1. Pool admin configures the allowlist: `allowedSwapper[pool][alice] = true` (only Alice may swap).
2. Pool admin also sets `allowedSwapper[pool][router] = true` so that Alice can use the standard router interface.
3. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. The pool calls `extension.beforeSwap(sender=router, ...)`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → Bob's swap succeeds.

The pool admin cannot simultaneously allow Alice to use the router and block Bob from using the same router, because the allowlist key is the router address, not the individual user.

### Impact Explanation

The swap allowlist guard is completely defeated for all router-mediated swaps once the router is allowlisted. Non-allowlisted users gain unrestricted swap access to a pool that was explicitly configured to restrict trading. Depending on the pool's purpose (e.g., a private LP pool, a restricted market-making venue), this allows adversarial traders to extract value from LPs at oracle-anchored prices, drain one-sided liquidity, or front-run restricted participants — all of which constitute direct loss of LP principal or owed assets above contest thresholds.

### Likelihood Explanation

Medium. The trigger requires the pool admin to allowlist the router, which is the natural and expected operational step for any admin who wants their whitelisted users to access the pool through the standard periphery interface. The research guidance for this extension explicitly flags this exact scenario: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* The bypass itself requires no privilege — any public user can call the router.

### Recommendation

The extension must gate the **actual end user**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention between the router and the extension.

2. **Check `sender` only for direct pool calls; for router calls, require the router to attest the real user**: Add a registry of trusted routers and require them to forward the real user address in a verifiable way.

The simplest safe default is to remove the router from the allowlist and require allowlisted users to call `pool.swap()` directly, documenting that router-mediated swaps are incompatible with per-user allowlisting under the current design.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true
  allowedSwapper[pool][router] = true   ← admin enables router for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router → pool.swap(msg.sender=router, ...)
    pool   → extension.beforeSwap(sender=router, ...)
    extension: allowedSwapper[pool][router] == true → no revert

  Result: bob swaps successfully on a pool that was supposed to block him.
  The allowlist guard is fully bypassed for all router users.
``` [1](#0-0) [2](#0-1) [4](#0-3)

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

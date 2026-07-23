### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, enabling allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool, which equals the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. This makes it structurally impossible to correctly gate individual users on router-mediated swaps: if the pool admin allowlists the router to enable router access for permitted users, every unpermitted user can bypass the guard by going through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first parameter the pool passes to the hook. The pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [2](#0-1) 

The router is `msg.sender` to the pool, so the pool forwards `router address` as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an impossible configuration for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (router address fails the check) |
| Allowlist the router | **Every** user — including non-allowlisted ones — can bypass the guard by routing through the router |

There is no configuration that correctly restricts router-mediated swaps to only the intended set of users.

The same structural problem exists for `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router's address. [3](#0-2) 

---

### Impact Explanation

When the pool admin allowlists the router (the natural step to enable router-mediated swaps for permitted users), the `SwapAllowlistExtension` guard is fully bypassed for all router-mediated swaps. Any unpermitted address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on a pool that was configured to be restricted. This breaks the core pool access-control invariant and constitutes an admin-boundary break: an unprivileged path (the public router) circumvents the configured allowlist guard.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router address, which is the natural and expected action when the admin wants allowlisted users to be able to use the standard router. The admin has no way to distinguish "router called by an allowlisted user" from "router called by anyone" at the extension level, so the bypass is an inevitable consequence of enabling router access.

---

### Recommendation

The pool should forward the **original initiating user** as `sender` to extensions, not the immediate `msg.sender`. One approach: require the router to pass the user's address explicitly (e.g., via `extensionData` or a dedicated parameter), and have the extension decode and verify it. Alternatively, the pool can expose a `swapWithSender(address trustedSender, ...)` entry point restricted to factory-registered routers, forwarding `trustedSender` to hooks instead of `msg.sender`. The `DepositAllowlistExtension` should be audited for the analogous payer/owner separation issue in `MetricOmmPoolLiquidityAdder`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook.
2. Pool admin allowlists the router: `swapExtension.setAllowedToSwap(pool, address(router), true)` — intending to allow router-mediated swaps for permitted users.
3. Non-allowlisted `attacker` calls `router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}))`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` to the pool is the router.
5. Pool calls `extension.beforeSwap(router, recipient, ...)` with `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → hook returns selector, no revert.
7. Attacker's swap executes on the restricted pool despite never being individually allowlisted. [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

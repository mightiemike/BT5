### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the user's address. Any pool that allowlists the router (the only way to let allowlisted users reach the pool through the supported periphery path) simultaneously opens the allowlist to every user on-chain.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- always the immediate caller of swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value, keyed by `msg.sender` (the pool):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` with itself as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,   // recipient = user-supplied
    ...
);
``` [3](#0-2) 

The pool therefore delivers `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Admin configuration | Effect |
|---|---|
| Allowlist individual users only, **not** the router | Allowlisted users cannot use the router; they must call the pool directly |
| Allowlist the router so allowlisted users can use the router | **Every** user on-chain can bypass the allowlist by routing through `MetricOmmSimpleRouter` |

There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users from using the router.

The same structural flaw applies to the multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also present the router as `sender`. [4](#0-3) 

---

### Impact Explanation

A pool admin deploys a curated pool (e.g., a KYC-gated or institution-only pool) and configures `SwapAllowlistExtension` with a specific set of allowlisted addresses. To let those users access the pool through the standard periphery, the admin must allowlist the router. Once the router is allowlisted, any unprivileged user can call `exactInputSingle` or `exactInput` through the router and execute swaps against the curated pool, completely bypassing the intended access control. This constitutes a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) circumvents a pool-level guard that the pool admin explicitly configured.

---

### Likelihood Explanation

The likelihood is **medium-to-high**. The `MetricOmmSimpleRouter` is the primary supported swap interface for end users. Any pool admin who wants allowlisted users to have a normal UX will allowlist the router. The bypass is then trivially reachable by any on-chain address with no special privileges, no flash loans, and no complex setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `tx.origin` as a fallback for known router callers**: When `sender` is a known router, the extension falls back to `tx.origin`. This is simpler but has known limitations with smart-contract wallets.

3. **Preferred — router-level identity forwarding**: Add an authenticated "on-behalf-of" field to the swap interface so the router can attest the originating user in a way the extension can verify (e.g., signed by the router, checked against a registry).

The root fix is that `SwapAllowlistExtension.beforeSwap` must not treat the router address as the identity to gate.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension; allowAllSwappers = false
  - allowedSwapper[pool][alice] = true   (alice is the only allowed swapper)
  - allowedSwapper[pool][router] = true  (admin adds this so alice can use the router)

Attack (executed by attacker, who is NOT allowlisted):
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(recipient=attacker, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES
  5. Swap executes; attacker receives output tokens from the curated pool

Result:
  - attacker, who was never allowlisted, successfully swaps against the curated pool
  - The SwapAllowlistExtension guard is completely bypassed
  - alice's allowlist entry is irrelevant to the bypass
``` [2](#0-1) [1](#0-0) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

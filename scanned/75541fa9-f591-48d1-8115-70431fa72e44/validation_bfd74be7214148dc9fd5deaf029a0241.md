### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the originating user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for permitted users), every unprivileged user can bypass the allowlist by calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the argument the pool forwards — which is `msg.sender` of the pool's own `swap` call:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The pool's `msg.sender` is the router contract. Therefore `sender` delivered to `beforeSwap` is the router address, not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, including users who are not individually allowlisted. The router is a public, permissionless contract — any address can call it.

The same bypass applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also call `pool.swap` with `msg.sender = router`. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates the `owner` (the economic actor), not `sender` (the immediate caller), so the deposit path does not share this flaw. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` and `allowAllSwappers = false` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers). Once the router is added to the allowlist — a necessary step if any permitted user is expected to trade through the standard periphery — the restriction is completely nullified. Any address can execute swaps against the pool by routing through `MetricOmmSimpleRouter`, receiving output tokens at oracle-derived prices without being individually permitted. This constitutes a broken core pool functionality (allowlist-gated swap flow) and a direct policy bypass with fund-flow consequences for the pool's LP providers and the protocol's curation model.

---

### Likelihood Explanation

The trigger is a semi-trusted but reasonable admin action: allowlisting the router so that permitted users can access the pool through the standard periphery. There is no on-chain warning that doing so opens the pool to all callers. The router is the primary user-facing swap entry point documented and deployed by the protocol, so allowlisting it is the expected operational step. Any pool that (a) uses `SwapAllowlistExtension` and (b) wants router support is vulnerable.

---

### Recommendation

The extension must gate the originating user, not the immediate pool caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated router + extension upgrade.

2. **Check `sender` only when `sender` is not a known periphery contract; otherwise check a user address embedded in `extensionData`**: More complex but backward-compatible.

The simplest correct fix is to align `SwapAllowlistExtension` with `DepositAllowlistExtension`'s pattern: gate the economically relevant actor. For swaps that actor is the address that initiated the transaction, which the router must explicitly forward.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  admin: allowAllSwappers[pool] = false
  admin: allowedSwapper[pool][alice] = true      // alice is permitted
  admin: allowedSwapper[pool][router] = true     // router added so alice can use it

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls extension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → check passes
    → swap executes, bob receives output tokens

Verification (direct call, correctly blocked):
  bob calls pool.swap(...) directly
    → pool calls extension.beforeSwap(sender=bob, ...)
    → allowedSwapper[pool][bob] == false → NotAllowedToSwap revert ✓

Result: bob bypasses the allowlist entirely via the router.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

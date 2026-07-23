### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user restriction by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` passes `msg.sender` of the pool's `swap()` call as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly. From the pool's perspective, `msg.sender = router`. The original user's identity is stored only in transient callback context as `payer` and is **never forwarded** to the pool as `sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

So `beforeSwap` sees `sender = router`, not the end user. The allowlist check is effectively applied to the router address, not to the individual trader.

This is structurally asymmetric with `DepositAllowlistExtension`, which correctly gates the `owner` (the economic beneficiary) rather than the immediate caller:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [4](#0-3) 

---

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of users (e.g., KYC'd counterparties) configures `SwapAllowlistExtension` and adds specific user addresses. To also support router-mediated swaps for those users, the admin adds the router to the allowlist. At that point, **any** unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutput`, `exactOutputSingle`) and the allowlist check passes because `sender = router` is allowlisted. The per-user restriction is completely bypassed.

Unauthorized traders can drain LP value from a pool that was designed to trade only with trusted counterparties, causing direct loss of LP principal.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user can call `exactInputSingle` with any pool address.
- The only precondition is that the pool admin has added the router to the allowlist — a natural and expected operational step when the admin wants to support router-mediated swaps for their allowlisted users.
- No privileged access, no malicious setup, and no non-standard tokens are required.

---

### Recommendation

The `beforeSwap` hook should check the **end user** identity, not the immediate caller. One approach is to pass the original user through `extensionData` from the router and verify it in the extension. A cleaner approach mirrors the deposit allowlist: define a canonical "swap owner" concept (analogous to `owner` in `addLiquidity`) that the router explicitly forwards, and gate on that identity instead of `sender`.

Alternatively, the extension can maintain a separate allowlist for approved routers and require that non-router callers are individually allowlisted, while router callers must supply a signed or verified end-user identity in `extensionData`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** add `attacker` to the allowlist.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `_beforeSwap` passes `sender = router` to the extension.
7. `allowedSwapper[pool][router] == true` → check passes.
8. `attacker` successfully swaps in a pool they were never authorized to access. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-39)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```

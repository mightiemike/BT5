### Title
`SwapAllowlistExtension` gates on the router address instead of the actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension evaluates the router's allowlist status — not the actual user's. If the router address is allowlisted, every user who routes through it bypasses the per-user swap allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whatever `msg.sender` the pool received when `swap()` was called. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [2](#0-1) 

So `msg.sender` to the pool is the router, and the pool passes the router address as `sender` to the extension. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This is structurally identical to the external bug: the wrong source address is used in the guard. The external contract used `address(this)` instead of `msg.sender` in a token pull; here the extension uses the intermediary router address instead of the actual economic actor.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly handles the operator pattern by ignoring `sender` and checking `owner` (the economic beneficiary):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [3](#0-2) 

`SwapAllowlistExtension` has no equivalent — it binds to the direct caller, which is the router when the standard periphery is used.

---

### Impact Explanation

**High.** If a pool admin allowlists the router address (a natural step when enabling router-based swaps on a curated pool), every user who routes through `MetricOmmSimpleRouter` passes the allowlist check regardless of their individual status. The per-user swap allowlist — the sole access-control boundary on a curated pool — is fully bypassed. Any unprivileged address can execute swaps against a pool that was configured to restrict trading to a specific set of counterparties, draining liquidity at oracle-derived prices.

A secondary impact exists even without the router being allowlisted: individually allowlisted users cannot use the standard periphery at all (the extension sees the router and reverts), making the core swap flow unusable through the supported entrypoint.

---

### Likelihood Explanation

**Medium.** A pool admin who wants to support both direct and router-based swaps on a curated pool will naturally allowlist the router. The admin's mental model is "I am allowing the router contract to call my pool," not "I am opening my pool to every user of the router." This is a realistic and foreseeable misconfiguration. The attacker requires no special privilege — any address can call `MetricOmmSimpleRouter.exactInputSingle` with the target pool.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual economic actor, not the direct caller. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the originating user (`msg.sender`) into `extensionData` so extensions can read the real actor.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode and check the user address from `extensionData` when `sender` is a known router, or the `IMetricOmmExtensions.beforeSwap` interface should be extended with an explicit `user` parameter analogous to `owner` in `beforeAddLiquidity`.

Until fixed, pool admins must not allowlist the router address on curated pools; they must require all allowlisted users to call `pool.swap()` directly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap hook enabled)
  admin: setAllowedToSwap(pool, alice, true)       // alice is the intended gated user
  admin: setAllowedToSwap(pool, router, true)       // admin enables router-based swaps

Attack:
  attacker (not in allowlist) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

  router → pool.swap(attacker, ...)          // msg.sender to pool = router
  pool  → extension.beforeSwap(router, ...) // sender = router
  extension checks allowedSwapper[pool][router] → true  ✓
  swap executes — attacker bypasses the per-user allowlist
``` [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

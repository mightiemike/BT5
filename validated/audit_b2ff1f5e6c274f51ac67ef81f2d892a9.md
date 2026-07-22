### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the original user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the original user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user, defeating the per-user curation the extension is meant to enforce.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

`msg.sender` inside `pool.swap()` is the router contract, so `sender` delivered to the extension is the router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Contrast with `DepositAllowlistExtension`**: the deposit extension correctly checks `owner` (the explicit position-owner parameter), which is always the actual depositor regardless of who calls `addLiquidity`. The swap extension has no equivalent "original user" parameter available to it.

```solidity
// DepositAllowlistExtension.sol line 38 — checks owner, not sender
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the `MetricOmmSimpleRouter` address (a natural operational step to support standard router-mediated swaps) inadvertently grants every user the ability to bypass the per-user allowlist. Any address can call `router.exactInputSingle()` targeting the pool and the extension will pass because `allowedSwapper[pool][router] == true`. The allowlist ceases to function as a curation gate, and disallowed users can drain or trade against the pool's liquidity.

### Likelihood Explanation

Medium. The trigger requires the pool admin to allowlist the router address. This is a natural and expected operational step for any curated pool that also wants to support the standard periphery UX. The admin has no on-chain signal that doing so collapses all per-user distinctions. The bypass is then reachable by any unprivileged user with no further preconditions.

### Recommendation

The extension should check the economically relevant actor, not the immediate caller. Two options:

1. **Pass the original initiator through `extensionData`**: the router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a convention between router and extension.

2. **Mirror the deposit extension pattern**: add an explicit `swapper` parameter to the swap interface (analogous to `owner` in `addLiquidity`) that the pool populates from a trusted source, so the extension always receives the original user regardless of routing path.

Until fixed, pool admins must not allowlist the router address on pools that rely on `SwapAllowlistExtension` for per-user curation.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable standard router UX for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(); msg.sender inside pool = router.
  - _beforeSwap passes sender = router to SwapAllowlistExtension.
  - Extension checks allowedSwapper[pool][router] == true → passes.
  - Attacker's swap executes against the curated pool's liquidity.

Expected: revert NotAllowedToSwap (attacker is not on the allowlist).
Actual:   swap succeeds; allowlist is bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

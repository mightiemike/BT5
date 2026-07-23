### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any caller to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user who calls through the router, completely defeating the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [1](#0-0) 

Here `msg.sender` is the pool and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap()` call:

```solidity
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap()
  recipient,
  ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

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
``` [3](#0-2) 

The pool sees `msg.sender` = router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same identity substitution occurs in `exactInput` for every hop: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly gates the economic actor by checking `owner` (the position owner explicitly passed to `addLiquidity`), not `sender` (the immediate caller):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [5](#0-4) 

`SwapAllowlistExtension` has no equivalent "owner" parameter to fall back on, so it is structurally unable to distinguish individual users when they route through the router.

---

### Impact Explanation

A pool admin who wants to allow router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. This single action grants every user who calls through `MetricOmmSimpleRouter` the ability to swap, regardless of whether they are individually allowlisted. Non-allowlisted users can drain LP assets from a pool that was intended to be restricted (e.g., KYC-gated, whitelist-only market-making pools). The allowlist invariant — "only approved addresses may swap" — is broken for the entire router-mediated path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and then enables router access (a natural operational step) triggers the bypass. No adversarial setup is required beyond a normal user calling the public router.

---

### Recommendation

The `SwapAllowlistExtension` should gate the actual initiating user, not the immediate caller. Two viable approaches:

1. **Encode the real user in `extensionData`**: The router forwards `msg.sender` (the original user) inside `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Mirror the deposit pattern**: Add an explicit `swapper` address parameter to the pool's `swap()` signature (analogous to `owner` in `addLiquidity`), and have the extension check that address. The router would pass `msg.sender` as the `swapper`.

Until fixed, pool admins should be warned that allowlisting the router address is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap extension.
2. Pool admin allowlists only user1:
       swapAllowlist.setAllowedToSwap(pool, user1, true)
3. Pool admin also allowlists the router to enable router-mediated swaps:
       swapAllowlist.setAllowedToSwap(pool, router, true)
4. user2 (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Pool

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

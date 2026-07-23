### Title
`SwapAllowlistExtension.beforeSwap()` gates on `sender` (router address) instead of the actual swapper, enabling full allowlist bypass through `MetricOmmSimpleRouter` - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. A pool admin who allowlists the router address to enable router-based swaps for their curated users inadvertently opens the pool to every user, completely defeating the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` performs:

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

`sender` is the first argument forwarded by the pool's `_beforeSwap()` call, which is always `msg.sender` of `pool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or any router entry point) calls the pool, `msg.sender` of `pool.swap()` is the **router contract**, not the end user:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,   // recipient
    params.zeroForOne,
    ...
);
``` [3](#0-2) 

The actual end user (`msg.sender` of the router call) is stored only in the router's transient callback context and is **never forwarded to the pool or the extension**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` — the position owner explicitly passed as a pool parameter — rather than `sender`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

The pool's `addLiquidity` passes `owner` as an explicit parameter, so the deposit extension can gate on the actual economic actor. No equivalent actor parameter exists for swaps; the extension is structurally bound to the wrong identity.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict swaps to a curated set of users, and then allowlists the router address so those users can trade via the standard periphery, inadvertently grants swap access to **every address** that calls the router. Any non-allowlisted user routes through `MetricOmmSimpleRouter`; the extension sees `sender = router`, which is allowlisted, and passes. The per-user curation is completely nullified. Unauthorized users can drain pool liquidity at oracle-derived prices, causing direct LP asset loss in a pool that was supposed to be access-controlled.

---

### Likelihood Explanation

The scenario is a natural operational mistake. A pool admin who deploys a curated pool with `SwapAllowlistExtension` will observe that allowlisted users cannot trade via the router (because the router is not allowlisted). The obvious remediation — allowlisting the router — silently removes all per-user protection. The protocol provides no warning, no documentation guard, and no test covering this interaction. The `FullMetricExtension` integration test only exercises direct pool calls through `TestCaller`, never through `MetricOmmSimpleRouter`. [5](#0-4) 

---

### Recommendation

The `beforeSwap` hook must gate on the actual economic actor, not the intermediary. Two viable approaches:

1. **Extend the pool's `swap()` signature** to accept an explicit `swapper` address (analogous to `owner` in `addLiquidity`), have the router forward `msg.sender` as that field, and have `SwapAllowlistExtension` check it instead of `sender`.

2. **Require the router to embed the real user in `extensionData`** and have `SwapAllowlistExtension` decode and verify it — but this requires the extension to trust the router's encoding, which introduces its own trust assumptions.

The simplest safe fix is option 1: add a `swapper` parameter to `pool.swap()` so the extension always receives the true initiating address regardless of which periphery contract is used.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as `extension1`, configured in `beforeSwap` order.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, userA, true)` — only `userA` is allowed.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — router is allowlisted so `userA` can use it.
4. `userB` (not allowlisted) calls `router.exactInputSingle(...)` targeting the pool.
5. The pool calls `_beforeSwap(msg.sender=router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `userB` successfully swaps despite not being on the allowlist, bypassing the curation entirely. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

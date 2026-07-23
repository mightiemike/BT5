Looking at the `SwapAllowlistExtension` hook, the `ExtensionCalling` dispatcher, and the `MetricOmmSimpleRouter` swap path together reveals a wrong-actor binding that lets any user bypass the per-pool swap allowlist.

---

### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. If the pool admin allowlists the router (a necessary step to enable any router-mediated swap on the pool), every unprivileged user can bypass the individual swapper allowlist by routing through the router.

---

### Finding Description

**Hook argument binding in `ExtensionCalling._beforeSwap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

**The allowlist check in `SwapAllowlistExtension.beforeSwap`:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is whoever called `pool.swap()`. When the user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

So the extension sees `sender = router address`. The check becomes `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes for **every user** who routes through it, regardless of whether they are individually allowlisted.

**Contrast with `DepositAllowlistExtension`**, which correctly gates by `owner` (the actual economic actor, passed as a parameter):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [5](#0-4) 

The deposit extension correctly identifies the economic actor (`owner`); the swap extension identifies only the direct caller (`sender`), which collapses to the router address on every router-mediated swap.

**The router's `_validatePath` also does not check for duplicate pools**, meaning a user can pass the same allowlisted pool twice in a multi-hop path, executing two swaps through the same pool in one transaction:

```solidity
function _validatePath(address[] calldata tokens, address[] calldata pools, bytes[] calldata extensionDatas)
    internal pure
{
    if (
        tokens.length < 2 || pools.length != tokens.length - 1 || extensionDatas.length != pools.length
            || pools.length > MAX_PATH_POOLS
    ) { revert InvalidPath(); }
}
``` [6](#0-5) 

No uniqueness check on `pools[]` exists, directly mirroring the external report's duplicate-adapter pattern.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is fully bypassed for any user who routes through `MetricOmmSimpleRouter`, provided the router is allowlisted. The pool admin cannot simultaneously:

1. Allow router-mediated swaps (requires allowlisting the router), and
2. Restrict individual users (allowlisting the router grants access to **all** router callers).

This breaks the core protection invariant of the extension: the allowlist no longer gates the economic actor (the end user) but only the intermediary (the router). Any disallowed user can trade on a curated pool by calling `exactInputSingle` or `exactInput` through the router.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted — a semi-trusted pool-admin action. A pool admin who wants to support router-mediated swaps for their allowlisted users will naturally allowlist the router, not realizing this grants access to all router callers. The inconsistency with `DepositAllowlistExtension` (which correctly uses `owner`) makes this a likely misconfiguration. The trigger itself (calling the public router) is fully unprivileged.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate the actual end user, not the direct pool caller. Two approaches:

1. **Extension-data forwarding**: The router passes the original `msg.sender` inside `extensionData`; the extension decodes and checks that address. The pool admin must trust the router to forward it honestly, so this requires a trusted-router model.
2. **Align with deposit pattern**: Introduce a `recipient` or `originator` field in the swap hook signature that the pool populates from a transient-storage context set by the router (similar to how the router already stores payer context in transient storage), so the extension always sees the true economic actor.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; allowAll = false.
2. Pool admin: extension.setAllowedToSwap(pool, router, true)
   // intended to enable router-mediated swaps for allowlisted users
3. Alice (NOT individually allowlisted) calls:
   router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
4. Router calls pool.swap(...) with msg.sender = router.
5. beforeSwap hook checks allowedSwapper[pool][router] == true → passes.
6. Alice's swap executes on the curated pool despite not being allowlisted.
7. Repeat with exactInput([pool, pool], ...) to execute two swaps on the
   same pool in one transaction (no duplicate-pool check in _validatePath).
``` [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L235-245)
```text
  function _validatePath(address[] calldata tokens, address[] calldata pools, bytes[] calldata extensionDatas)
    internal
    pure
  {
    if (
      tokens.length < 2 || pools.length != tokens.length - 1 || extensionDatas.length != pools.length
        || pools.length > MAX_PATH_POOLS
    ) {
      revert InvalidPath();
    }
  }
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

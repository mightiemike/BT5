### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the **router contract**, not the end-user. A pool admin who allowlists the router (required for any router-based swap to succeed) inadvertently opens the pool to **all users**, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (enforced by `onlyPool` in `BaseMetricExtension`). The `sender` argument is whatever the pool passes as the first argument to `IMetricOmmExtensions.beforeSwap`. In `ExtensionCalling._beforeSwap`, the pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

Inside `pool.swap()`, `msg.sender` is the **router address**. The pool passes this as `sender` to `_beforeSwap`, so the extension evaluates:

```
allowedSwapper[pool][router]   // NOT allowedSwapper[pool][end_user]
```

The actual end-user identity (`msg.sender` of the router call) is stored only in the router's transient callback context for payment purposes and is **never forwarded to the pool or the extension**.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties). Two outcomes both break the invariant:

1. **Admin does NOT allowlist the router**: Individually allowlisted users cannot swap through the standard periphery router at all — the extension reverts with `NotAllowedToSwap` because the router is not in the allowlist. Core swap functionality is broken for the intended users.

2. **Admin DOES allowlist the router** (the only way to enable router-based swaps): The check becomes `allowedSwapper[pool][router] == true`, which passes for **every** end-user who routes through the router, regardless of whether they are individually allowlisted. The allowlist is completely bypassed. Any unprivileged user can trade on a pool that was designed to be curated.

Scenario 2 is the fund-impacting path: unauthorized users gain swap access to a pool whose oracle-anchored prices and liquidity were provisioned under the assumption that only vetted counterparties would trade.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary supported periphery swap entrypoint; most users are expected to route through it.
- A pool admin who wants allowlisted users to use the router **must** allowlist the router, triggering the bypass automatically.
- No special privileges, flash loans, or exotic token behavior are required — a standard `exactInputSingle` call suffices.
- The `DepositAllowlistExtension` correctly gates by `owner` (the position owner), so the deposit path does not share this flaw; the swap path is uniquely broken. [5](#0-4) 

---

### Recommendation

The pool must forward the original end-user identity to the extension. Two approaches:

1. **Add a `payer`/`originator` field to the swap call**: Extend `pool.swap()` to accept an explicit `originator` address (set by the router to `msg.sender` before calling the pool) and pass it as `sender` to extension hooks instead of `msg.sender`.

2. **Check `recipient` instead of `sender` in the extension**: If the pool's design guarantees that `recipient` is always the end-user (it is in `exactInputSingle`), the extension could gate by `recipient`. However, this breaks for multi-hop flows where intermediate recipients are the router itself.

The cleanest fix is approach 1: the router stores the real user in transient storage (it already does this for the payer) and the pool reads it as the canonical `sender` for extension dispatch.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for router to work

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient=bob, ...)
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true  → passes
  - bob's swap executes on the curated pool

Result:
  - bob, who is not in the allowlist, successfully swaps
  - The allowlist invariant is broken for all router-mediated swaps
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

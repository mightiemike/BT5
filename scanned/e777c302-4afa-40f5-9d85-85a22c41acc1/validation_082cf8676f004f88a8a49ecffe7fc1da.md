### Title
`SwapAllowlistExtension` gates on router address instead of actual end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` on the pool, so the extension sees the router address — not the actual end-user. If the pool admin allowlists the router (the natural configuration for pools that want to support router-mediated swaps for their curated users), every unpermissioned user can bypass the individual allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong actor in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the first argument forwarded by the pool, which is `msg.sender` of the `swap()` call itself.

**How the pool binds `sender`:** [2](#0-1) 

The pool passes `msg.sender` as `sender` to `_beforeSwap`. When a user calls the pool directly, `sender = user`. When a user calls through `MetricOmmSimpleRouter`, the router calls `pool.swap()`, so `sender = router address`.

**How the router calls the pool:** [3](#0-2) 

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
```

The router calls `pool.swap()` directly with no forwarding of the original `msg.sender`. The pool records `msg.sender = router` and passes it as `sender` to every extension hook.

**The bypass path:**

A pool admin who wants to support router-mediated swaps for their curated users will allowlist the router address:

```
setAllowedToSwap(pool, routerAddress, true)
```

Once the router is allowlisted, the check `allowedSwapper[pool][sender]` evaluates to `true` for every call that arrives through the router — regardless of who the actual end-user is. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will pass them through.

The `DepositAllowlistExtension` does not share this flaw — it correctly gates on `owner` (the position owner), not `sender` (the payer/router). [4](#0-3) 

---

### Impact Explanation

Any user blocked by the swap allowlist can execute swaps on a curated pool by routing through the public `MetricOmmSimpleRouter`. The allowlist — the only on-chain mechanism for restricting swap access — is completely neutralized for all router-mediated paths. Consequences include:

- Unauthorized users trading on pools intended for KYC/AML-compliant or otherwise curated participants.
- Violation of the pool's access-control invariant, which is the core protection the extension was deployed to enforce.
- Direct financial impact: disallowed users can drain liquidity or extract value from pools that were designed to serve only a restricted set of counterparties.

This matches the **allowlist bypass** impact class: a curated pool's allowlist is bypassed through a public periphery path, causing direct loss of curation policy and potential fund impact.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is the natural and expected configuration for any pool that:

1. Deploys `SwapAllowlistExtension` to restrict individual users, **and**
2. Wants those users to be able to use the standard periphery router (the documented user-facing entry point).

A pool admin following the protocol's intended usage pattern — allowlist specific users, support the router — will unknowingly open the bypass. The attacker needs no special privileges: any EOA can call `MetricOmmSimpleRouter` permissionlessly.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end-user, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData` for each hop, and the extension decodes and checks it when `sender` is a known router address.
2. **Separate router-aware allowlist**: Add a second mapping `allowedRouterSwapper[pool][actualUser]` and require the router to pass the user identity in a signed or authenticated payload.

The simplest safe rule: document that allowlisting the router address is equivalent to `setAllowAllSwappers(pool, true)` and enforce this in the admin setter or NatSpec so pool admins cannot accidentally open the bypass.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  pool admin: setAllowedToSwap(pool, routerAddress, true) // support router for alice

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: charlie,
        ...
    })

  Router calls pool.swap(charlie, ...) — msg.sender in pool = router
  Pool calls _beforeSwap(router, charlie, ...)
  Extension checks: allowedSwapper[pool][router] == true  ✓
  Extension passes — charlie's swap executes
``` [5](#0-4) [6](#0-5) [2](#0-1)

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

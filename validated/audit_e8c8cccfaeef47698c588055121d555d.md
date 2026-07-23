### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Pool's Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. A pool admin who allowlists the router address to enable router-mediated swaps for their permitted users inadvertently opens the pool to every caller of the router, completely defeating the allowlist guard.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct key for the per-pool mapping). `sender` is the value forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's own `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

So `msg.sender` inside `pool.swap()` is the **router**, not the original user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The bypass path:**

A pool admin who wants to allow their allowlisted users to use the standard periphery router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every call that arrives through the router — regardless of who the actual end user is. Any unprivileged address can call `router.exactInputSingle(pool, ...)` and the allowlist guard passes unconditionally.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool. When the router is allowlisted (the only way to support router-mediated swaps for permitted users), the guard is silently nullified for all callers. Any address can drain liquidity from a pool that was intended to be private or restricted to specific counterparties. LPs who deposited into a restricted pool expecting only vetted counterparties are exposed to unrestricted swap flow, which can move the pool cursor, consume reserved liquidity depth, and alter fee accrual in ways they did not consent to. This constitutes broken core pool functionality and an admin-boundary break where an unprivileged path bypasses an admin-configured access control.

---

### Likelihood Explanation

The likelihood is medium-high. The `SwapAllowlistExtension` is a production periphery contract. Any operator of a restricted pool who wants their allowlisted users to use the standard router — the primary user-facing entry point — must allowlist the router. The documentation for `addLiquidity` already notes the operator pattern (`msg.sender` pays but need not equal `owner`), signaling that intermediary contracts are expected. A pool admin following the natural integration path will trigger the bypass without any indication that doing so opens the pool to all callers.

---

### Recommendation

The extension must gate the **original end user**, not the intermediate contract. Two options:

1. **Pass the original initiator through the router.** The router stores the original `msg.sender` in transient storage (already done for the callback payer via `_setNextCallbackContext`). Extend this to also store the initiator and have the pool forward it as a separate field in `extensionData` or as an additional hook argument.

2. **Check `sender` only when it is not a known router; otherwise read the initiator from `extensionData`.** The extension can require that router-mediated calls include the real user address in `extensionData` and verify it against the allowlist, while direct calls continue to use `sender`.

Option 1 is cleaner and does not require callers to supply extra data.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  pool admin calls setAllowedToSwap(pool, userA, true)
  pool admin calls setAllowedToSwap(pool, address(router), true)
    ↑ necessary so userA can use the router

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: userB})

  router calls:
    pool.swap(userB, zeroForOne, amount, priceLimit, "", extensionData)
    ↑ msg.sender inside pool.swap() = address(router)

  pool calls:
    _beforeSwap(address(router), userB, ...)

  extension checks:
    allowedSwapper[pool][address(router)] → true  ← bypass succeeds

  userB receives swap output; allowlist guard never saw userB's address.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

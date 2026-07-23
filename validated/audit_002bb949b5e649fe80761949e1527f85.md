### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the **actual user**. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to every user, completely defeating the allowlist.

---

### Finding Description

**Actor binding in the pool's `swap` function**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every before-swap extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L230-L240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap
    recipient,
    ...
);
```

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  L72-L80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

When the router calls `pool.swap`, the pool's `msg.sender` is the **router address**, so `sender` forwarded to the extension is the router, not the original user.

**Extension check**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  L37-L39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Inside the extension:
- `msg.sender` = pool (the pool called the extension — correct, enforced by `onlyPool`)
- `sender` = **router address** (not the original user)

So the check resolves to `allowedSwapper[pool][router]`. If the router is in the allowlist, every user who calls the router passes the guard regardless of their own address.

**The catch-22**

A pool admin who wants to support both an allowlist and router-based swaps must allowlist the router. But allowlisting the router grants every user on the planet the ability to swap on the pool, making the allowlist meaningless. There is no way to configure the extension to check the actual user when the swap arrives via the router.

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool where the router address has been added to `allowedSwapper`. The allowlist is the primary access-control mechanism for curated pools; bypassing it exposes LP funds to unrestricted trading by adversarial or non-permitted counterparties, which can cause direct LP losses (e.g., informed-trader adverse selection on a pool intended for a closed set of market participants).

---

### Likelihood Explanation

The likelihood is **medium**. The scenario requires the pool admin to allowlist the router, which is a natural operational step for any curated pool that also wants to support the standard periphery UX. The admin has no way to know that doing so opens the pool to all users, because the extension's `isAllowedToSwap` view function returns `true` for the router address, not for arbitrary users. Once the router is allowlisted, the bypass is trivially reachable by any address with zero additional privileges.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user**, not the intermediate caller. Two complementary fixes:

1. **Pass the original user through the router**: The router should forward `msg.sender` (the actual user) as an explicit field in `extensionData`, and the extension should decode and check that field when `sender` is a known router.

2. **Alternatively, change the pool's hook signature**: Pass both the immediate caller (`msg.sender`) and an optional "on-behalf-of" address so extensions can always check the economically relevant actor. This mirrors the `addLiquidity` design where both `sender` and `owner` are forwarded.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to setting `allowAllSwappers = true`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as a before-swap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only Alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // to support router UX

Attack:
  - Eve (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)
  - Pool calls extension.beforeSwap(router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for Eve despite her not being in the allowlist

Result:
  - Eve trades on a curated pool she was never permitted to access.
  - LP funds are exposed to an unrestricted counterparty.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

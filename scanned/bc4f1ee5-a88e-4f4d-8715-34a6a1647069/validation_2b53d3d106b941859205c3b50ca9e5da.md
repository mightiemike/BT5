### Title
SwapAllowlistExtension Checks Immediate Caller (Router) Instead of Actual User, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, any unprivileged user can bypass the per-user allowlist entirely by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension hook), and `sender` is the first argument — which the pool sets to its own `msg.sender` (the immediate caller of `pool.swap()`):

```solidity
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  recipient, zeroForOne, amountSpecified, priceLimitX64,
  packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` as `msg.sender = router`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
``` [3](#0-2) 

The pool therefore calls `extension.beforeSwap(sender=router, ...)`. The extension checks `allowedSwapper[pool][router]`, **not the actual user's address**. The router passes empty `callbackData` (`""`), and the `extensionData` is user-controlled but the extension ignores it (the last `bytes calldata` parameter is unnamed and unused): [1](#0-0) 

There is no mechanism by which the router communicates the original user's identity to the extension. The `_setNextCallbackContext` stores the payer for the payment callback only — it is never forwarded to the extension hook. [4](#0-3) 

---

### Impact Explanation

If the pool admin allowlists the router address (which is the natural action to take when allowlisted users need to use the router), the `SwapAllowlistExtension` guard is completely neutralized: **any unprivileged user can swap in the restricted pool by routing through `MetricOmmSimpleRouter`**. The pool admin's intended per-user access control is bypassed entirely. This is an admin-boundary break — an unprivileged path (the public router) circumvents a configured pool guard — matching the allowed impact gate.

---

### Likelihood Explanation

Medium-High. A pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to be able to use the standard router will allowlist the router address. This is the only way to make router-mediated swaps work for those users. Once the router is allowlisted, the bypass is available to every address on-chain with no special privileges required.

---

### Recommendation

- **Short term**: The `SwapAllowlistExtension` should not rely solely on the `sender` argument for identity when the sender may be an intermediary. One approach: require that the router encodes the original `msg.sender` in `extensionData`, and have the extension verify it against a trusted-router registry. Alternatively, document explicitly that the allowlist only applies to direct `pool.swap()` calls and that the router must never be allowlisted.
- **Long term**: Define a protocol-level convention for passing the originating user through `extensionData` on all periphery entry points, and enforce it in the base extension.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Eve (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: eve, ...})`.
5. Router calls `pool.swap(recipient=eve, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Eve's swap executes successfully in the restricted pool, bypassing the per-user allowlist entirely. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L31-32)
```text
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

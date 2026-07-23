### Title
SwapAllowlistExtension gates the router's address instead of the actual swapper, making allowlisted-pool swaps via MetricOmmSimpleRouter always revert - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool that uses this extension with a per-user allowlist becomes completely unusable through the router for allowlisted users, breaking the core swap flow.

---

### Finding Description

The call chain is:

```
User EOA
  → MetricOmmSimpleRouter.exactInputSingle() / exactInput()
      → IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, ..., extensionData)
          // msg.sender to pool = router address
          → MetricOmmPool._beforeSwap(msg.sender=ROUTER, recipient, ...)
              → ExtensionCalling._callExtensionsInOrder(BEFORE_SWAP_ORDER, ...)
                  → SwapAllowlistExtension.beforeSwap(sender=ROUTER, ...)
                      // checks allowedSwapper[pool][ROUTER]  ← wrong identity
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← router address, not the actual user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks the allowlist against that `sender`:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

Because `sender` = router address, the check is `allowedSwapper[pool][router]`. If the pool admin has allowlisted specific user EOAs (the intended use), the router is not in the allowlist and every router-mediated swap reverts with `NotAllowedToSwap`.

This is structurally identical to the external bug: a hook receives an unexpected address (router instead of user, analogous to `address(0)` instead of the real recipient during a burn), causing a guard check to fail and blocking a legitimate operation entirely.

Note the asymmetry with `DepositAllowlistExtension`, which correctly gates the `owner` parameter (the actual position owner, explicitly passed through the liquidity adder), not the `sender`. No equivalent "real user" parameter exists in the swap interface, so the swap allowlist has no way to recover the original user's identity.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` and a per-user allowlist becomes completely unusable through `MetricOmmSimpleRouter` for all allowlisted users. The only workaround is for users to call `pool.swap()` directly, which requires implementing the `IMetricOmmSwapCallback` interface themselves — not a standard user flow. The core swap path through the periphery router is broken for the entire class of allowlisted pools.

The alternative "fix" available to the pool admin — allowlisting the router address — defeats the purpose of the extension entirely: any user can then route through the router and bypass the per-user gate, turning a selective allowlist into an open pool.

Impact: **broken core swap flow** for allowlisted pools using the standard periphery router.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` with per-user entries (the primary documented use case) and expects users to interact via `MetricOmmSimpleRouter` (the primary documented periphery entry point) will hit this immediately on the first router-mediated swap. No special attacker action is required; normal user behavior triggers the revert.

---

### Recommendation

The `beforeSwap` hook should gate the economically relevant actor. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient (the address receiving output tokens) is often the meaningful identity. This is already correctly set by the router (`params.recipient`).

3. **Align with `DepositAllowlistExtension`**: Add an explicit "swapper" field to the swap interface analogous to `owner` in `addLiquidity`, so the router can forward the original user's address as a first-class parameter rather than relying on `msg.sender`.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension configured.
// Pool admin allowlists alice (EOA) for swapping.
extension.setAllowedToSwap(address(pool), alice, true);

// Alice tries to swap through the standard router.
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
  pool: address(pool),
  recipient: alice,
  zeroForOne: true,
  amountIn: 1000,
  ...
}));
// ↑ Reverts with NotAllowedToSwap because:
//   pool.swap() is called by the router → msg.sender to pool = router
//   _beforeSwap(sender=router, ...) → extension checks allowedSwapper[pool][router]
//   router is not allowlisted → revert

// Alice calling pool.swap() directly (implementing the callback herself) would succeed,
// but that is not the standard user flow.
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

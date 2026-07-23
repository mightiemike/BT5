### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `swap` call. When `MetricOmmSimpleRouter` mediates the swap, `msg.sender` at the pool is the router, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the gate to every user, because the router is a single shared address. Any unprivileged user can bypass the per-user restriction by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

At the pool level `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual user's address is never forwarded to the pool or the extension.

**Contrast with `DepositAllowlistExtension`:** The deposit allowlist checks `owner` (the second argument), which is the position's economic beneficiary and is preserved through `MetricOmmPoolLiquidityAdder` regardless of who the payer is:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The swap allowlist has no equivalent "owner" parameter to fall back on; it can only see the direct caller.

---

### Impact Explanation

A pool admin who configures a private pool (e.g., restricted to a whitelist of institutional counterparties) and allowlists the router so that approved users can trade via the standard periphery inadvertently grants every user on-chain the ability to swap. Any unprivileged address calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool; the extension sees `sender = router` (allowlisted), passes the check, and the swap executes. LP assets in the restricted pool are exposed to unrestricted trading, defeating the access-control invariant the pool admin configured.

---

### Likelihood Explanation

The scenario is directly reachable by any user with no special privileges. The only precondition is that the pool admin has allowlisted the router — a natural and expected action for any pool that wants to support the standard periphery for its approved users. No malicious setup is required; the attacker simply calls the public router.

---

### Recommendation

The `SwapAllowlistExtension` must gate the actual end user, not the intermediary. Two options:

1. **Forward the real user via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Align with the deposit model:** Add a `swapper` parameter to the pool's `swap` call (analogous to `owner` in `addLiquidity`) so the actual user identity is always available to extensions independent of the intermediary.

Until fixed, pool admins must not allowlist the router on restricted pools and must require all approved users to call `pool.swap` directly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (intending to let approved users trade via the router)
  pool admin does NOT allowlist attacker EOA

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})
  router calls pool.swap(recipient, ...) → msg.sender = router
  _beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  allowedSwapper[pool][router] == true → check passes
  swap executes for attacker despite attacker not being on the allowlist
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

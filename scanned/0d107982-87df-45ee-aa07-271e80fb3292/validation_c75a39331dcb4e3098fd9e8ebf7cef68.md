### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router to support periphery-mediated swaps, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:162-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
    sender,       // ← router address when routed
    recipient,
    ...
))
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool (correct key), sender = router (wrong actor)
```

When `MetricOmmSimpleRouter.exactInputSingle()` is used, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The pool receives `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual end user's identity is never presented to the guard.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address (a natural step to support the standard periphery) inadvertently opens the gate to every user. Any address — including those explicitly not allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput` and execute swaps on the curated pool. The allowlist policy is completely nullified for all router-mediated paths. Unauthorized traders can drain LP-owned token reserves at oracle-derived prices, causing direct loss of LP principal.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public swap entry point documented and deployed alongside the core pool. Pool admins who want to support normal user flows will allowlist the router. The bypass requires no privileged access, no special tokens, and no unusual setup — any EOA can call the public router functions. The combination of a natural admin action (allowlisting the router) and a public entry point (the router) makes exploitation straightforward and highly likely once a curated pool is live.

### Recommendation

The `SwapAllowlistExtension` must gate the **actual end user**, not the intermediary contract. Two approaches:

1. **Pass the original user through the router**: Add a `swapper` parameter to the router's swap calls (or encode it in `extensionData`) and have the extension read the real user identity from there, verified against the router's transient callback context.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is that the recipient is the economically relevant actor, gate on `recipient` rather than `sender`. This is consistent with the deposit allowlist, which gates on `owner` (the position beneficiary) rather than `sender` (the payer/operator).

The deposit allowlist (`DepositAllowlistExtension`) already uses the correct pattern — it checks `owner` (the position beneficiary), not `sender` (the operator/adder contract). The swap allowlist should adopt the same principle and check `recipient` or a user identity passed through `extensionData`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists router: swapExtension.setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  2. router calls pool.swap(recipient=attacker, ...) — msg.sender to pool = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES
  5. Swap executes; attacker receives tokens from the curated pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist fully bypassed
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

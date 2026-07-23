### Title
SwapAllowlistExtension Gates Router Address Instead of Real Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end-user. If the pool admin allowlists the router to permit any router-mediated swap, every non-allowlisted user can bypass the guard by calling through the router.

### Finding Description
In `SwapAllowlistExtension.beforeSwap`, the guard is:

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

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the `sender` argument forwarded from `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap()` call.

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The actual user's address (`msg.sender` of the router) is stored only in the transient callback context for payment settlement. It is never passed to `pool.swap()`. The pool sees `msg.sender = router`. `ExtensionCalling._beforeSwap` therefore passes `sender = router` to the extension.

The extension then evaluates `allowedSwapper[pool][router]`. If the router is allowlisted—which the admin must do to allow any router-mediated swap for legitimate users—every user, including non-allowlisted ones, passes the check by routing through the same public router.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position recipient), which is explicitly passed by the caller and validated by `_validateOwner`, so the gated identity is the economic beneficiary, not the operator.

### Impact Explanation
The `SwapAllowlistExtension` is the pool admin's mechanism to restrict swaps to trusted counterparties (e.g., KYC'd addresses, whitelisted market makers, or institutional participants). Once the router is allowlisted to support any legitimate router user, the guard becomes a no-op for all router-mediated calls. Non-allowlisted users—including MEV bots and informed traders—can freely swap in a pool designed to be private. LPs in such a pool suffer adverse-selection losses because the toxic flow the allowlist was meant to block is now unrestricted. This is a medium-severity admin-boundary break: an unprivileged path (the public router) bypasses a pool-admin-configured access control gate with direct LP fund impact.

### Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and also wants to support router-mediated swaps for at least one legitimate user must allowlist the router. This is the normal operational pattern: the admin allowlists the router so that approved users can use the standard periphery. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges.

### Recommendation
The extension must gate the actual end-user, not the intermediary. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the actual user address in `extensionData` and have `SwapAllowlistExtension` decode and verify it (requires the extension to trust the router, which can be enforced by checking `sender` is a known router before accepting the forwarded identity).
2. **Direct-call-only documentation**: Explicitly document that `SwapAllowlistExtension` only enforces access control for direct `pool.swap()` calls and is incompatible with router-mediated flows; provide a separate extension that reads the payer from the transient callback context.

### Proof of Concept

1. Deploy pool with `SwapAllowlistExtension` configured.
2. Admin allowlists `userA` (a legitimate user) and the router so `userA` can use the periphery:
   - `allowedSwapper[pool][userA] = true`
   - `allowedSwapper[pool][router] = true`
3. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap()` → pool calls `_beforeSwap(sender=router, ...)` → extension checks `allowedSwapper[pool][router] == true` → guard passes.
5. `userB` completes the swap in a pool that was supposed to block them.

Conversely, if the admin does not allowlist the router, `userA` also cannot use the router, making the standard periphery unusable for all allowlisted users—demonstrating that there is no configuration that achieves the intended "allow specific users through the router" semantics. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

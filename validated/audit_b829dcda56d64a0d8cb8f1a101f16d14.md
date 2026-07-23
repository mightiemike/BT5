The code confirms all three key facts in the claim. Let me verify the extension calling mechanism to be thorough.

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Allowlist via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender` — the router address when swaps are routed through `MetricOmmSimpleRouter`. If a pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by calling the router, because the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][endUser]`. There is no configuration that simultaneously enforces per-user restrictions and supports router-mediated swaps.

## Finding Description

The full call chain is:

1. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly — so inside `pool.swap()`, `msg.sender` is the router address. [1](#0-0) 

2. `MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`, which propagates it as `sender` to all configured extensions. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` encodes `sender` as the first argument in the `abi.encodeCall` to `IMetricOmmExtensions.beforeSwap`, so the extension receives the router address as `sender`. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool (the pool calls the extension) and `sender` is the router address. The end user's address is never checked. [4](#0-3) 

This creates an irresolvable dilemma: allowlisting individual users prevents them from using the router (the primary swap interface), while allowlisting the router opens the pool to all users. No existing guard in the router or pool passes the original `msg.sender` (the end user) to the extension — `params.extensionData` is forwarded verbatim from the user's calldata and the extension does not decode it.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the position owner, the second argument), which is independent of who calls the pool. [5](#0-4) 

## Impact Explanation

A pool admin deploys a curated pool (e.g., KYC-gated or institutional-only) with `SwapAllowlistExtension` and allowlists specific user addresses. To support the standard router interface, the admin also allowlists the router. Once the router is allowlisted, any unprivileged user can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` targeting the curated pool and bypass the allowlist entirely. LP capital deposited under the assumption of a restricted pool is exposed to unauthorized traders. This is broken core pool functionality — the allowlist extension fails to enforce its stated invariant for all router-mediated swaps.

## Likelihood Explanation

The router is the primary supported swap interface. Any user can call it permissionlessly. The bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices. The pool admin has no way to prevent this without removing the router from the allowlist entirely, which breaks router support for all users including allowlisted ones.

## Recommendation

The extension must check the end user's identity, not the immediate caller of the pool. Two viable approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool, and the extension decodes and verifies it. This requires a convention between the router and the extension.

2. **Separate `sender` from `initiator` in the hook interface**: The pool passes both `msg.sender` (the direct caller) and an additional `initiator` field (populated by the router via a trusted mechanism) to the extension, and the extension checks `initiator`.

## Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists alice (KYC'd) and the router (to support router swaps)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted
// bob calls the router — extension sees sender = router (allowlisted) → passes
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// bob successfully swaps on a pool he was never allowlisted for
// allowedSwapper[pool][bob] == false, but allowedSwapper[pool][router] == true
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the pool admin allowlists the router (the only way to let legitimate users use the standard periphery), every unprivileged address can bypass the allowlist entirely by routing through the router.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, …)`. When the router is the caller, `msg.sender` is the router contract, so `sender` forwarded to every extension is the router address. [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Allowlist the router | Every address on-chain can bypass the allowlist by routing through the router |
| Do not allowlist the router | Allowlisted users cannot use the standard periphery at all | [3](#0-2) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and trade on the restricted pool as long as the router is allowlisted. This constitutes a direct admin-boundary break: an unprivileged path (`router → pool`) circumvents the access control the pool admin configured. [4](#0-3) 

### Likelihood Explanation

The bypass is trivially reachable by any address. The router is a public, permissionless contract. No special role, flash loan, or multi-step setup is required — a single `exactInputSingle` call suffices. The only precondition is that the pool admin has allowlisted the router (which is the natural action to take when deploying a curated pool that is still meant to be usable via the standard periphery).

### Recommendation

Pass the **original end-user** through the swap call chain so the extension can gate on the correct actor. Two concrete approaches:

1. **Preferred — pass `tx.origin` or a verified payer field**: Have the router encode the actual user in `extensionData` and have the extension decode and verify it. The pool already forwards `extensionData` to every hook.
2. **Alternative — check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is the recipient of output tokens. Gating on `recipient` is harder to spoof via the router.

The `DepositAllowlistExtension` correctly gates on `owner` (the LP position owner), not `sender`, which is why it does not share this flaw. [5](#0-4) 

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended gated user
  3. Admin calls setAllowedToSwap(pool, router, true)  // necessary for alice to use the router

Attack (Bob, not allowlisted):
  4. Bob calls router.exactInputSingle({pool: pool, tokenIn: token0, ...})
     → pool.swap(recipient, zeroForOne, amount, ...) is called with msg.sender = router
     → _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true  ✓
     → swap executes for Bob despite Bob never being allowlisted

Result:
  Bob trades on a pool that was supposed to be restricted to alice only.
  The allowlist invariant is broken; any address can trade by routing through the router.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

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

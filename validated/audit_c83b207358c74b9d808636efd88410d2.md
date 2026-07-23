### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router (which is required for any legitimate user to swap through the router), every user—including those not on the allowlist—can bypass the per-user restriction by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap()` performs its identity check as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (used as the mapping key) and `sender` is the first argument forwarded by the pool. The pool always sets this argument to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The pool's `msg.sender` is now the **router**, so `sender` delivered to the extension is the router's address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

Contrast this with `DepositAllowlistExtension`, which correctly checks `owner` (the position owner explicitly passed through the call chain), not `sender` (the immediate caller):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [5](#0-4) 

The deposit extension is safe because `owner` is an explicit parameter that the pool passes through unchanged. The swap extension is broken because it relies on `sender` = `msg.sender` of the pool call, which collapses to the router address for all router-mediated swaps.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants legitimate allowlisted users to swap through the router **must** call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the extension's check passes for **any** user who routes through the router—regardless of whether that user is on the allowlist. The per-user curation is completely defeated. Any non-allowlisted user can trade on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), receiving the same execution as an allowlisted user.

This is a direct policy bypass with fund-impacting consequences: unauthorized users gain access to a pool that was explicitly configured to restrict trading, which can drain LP value, violate regulatory or compliance constraints, or undermine the economic model of the curated pool.

### Likelihood Explanation

The likelihood is **High**. The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router, which simultaneously opens the pool to all users. There is no way to support router-based swaps for legitimate users while enforcing per-user restrictions, because the router's address is the only identity the extension sees. The bypass requires no special privileges, no flash loans, and no multi-transaction setup—any user can exploit it in a single `exactInputSingle` call.

### Recommendation

The `SwapAllowlistExtension` should check the **original user** rather than the immediate pool caller. Two approaches:

**Option A (preferred):** Pass the original user's address through `extensionData`. The router encodes `msg.sender` into `extensionData`, and the extension decodes and checks it. This requires the extension to trust that the router correctly encodes the user, which is acceptable since the router is a known periphery contract.

**Option B:** Add an explicit `originalSender` field to the `beforeSwap` hook signature (analogous to how `addLiquidity` separates `sender` from `owner`), so the pool can forward the true originator. This is a core interface change but provides a clean, unforgeable identity.

At minimum, the documentation for `SwapAllowlistExtension` must warn that allowlisting the router grants access to all router users, and pool admins who need per-user enforcement must not allowlist the router.

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Pool admin allowlists the router so that user Alice (allowlisted) can swap via router.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Alice is allowlisted directly AND via router.

// Bob is NOT allowlisted.
// Direct swap by Bob reverts:
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, false, 1000, type(uint128).max, "", "");

// But Bob routes through the router — extension sees router address, which IS allowlisted:
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token1,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob's swap succeeds — allowlist bypassed.
``` [6](#0-5) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
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

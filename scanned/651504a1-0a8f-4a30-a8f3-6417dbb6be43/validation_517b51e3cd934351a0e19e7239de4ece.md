### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` received by the extension is the router — not the actual end user. If the pool admin allowlists the router (the natural configuration for any pool that supports router-mediated swaps), every non-allowlisted user can bypass the guard by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

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

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool's `_beforeSwap` dispatcher:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap,
            (sender, recipient, ...))
    );
}
``` [2](#0-1) 

The pool passes its own `msg.sender` (the immediate caller of `pool.swap()`) as `sender` to `_beforeSwap`. When a user swaps through `MetricOmmSimpleRouter.exactInputSingle`, the router calls:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., "", params.extensionData
);
``` [3](#0-2) 

The pool's `msg.sender` is the router, so `sender = router`. The allowlist check resolves to `allowedSwapper[pool][router]`. The actual end user's address is never consulted.

The router passes `""` as callback data and user-supplied `extensionData` as extension data — neither carries the real user's identity to the extension. There is no mechanism for the router to forward the actual swapper's address to `SwapAllowlistExtension`.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise approved addresses must also allowlist the router for any router-mediated swaps to function. Once `allowedSwapper[pool][router] = true`, the allowlist check `allowedSwapper[pool][router]` passes for every swap routed through `MetricOmmSimpleRouter`, regardless of who the actual end user is. Every non-allowlisted user can bypass the guard by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router. The allowlist extension is rendered completely ineffective for router-mediated swaps, which is the primary public swap path.

The inverse failure also exists: if the admin does not allowlist the router, allowlisted users cannot use the router at all, breaking the core swap flow for those users.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is only meaningful when `allowAllSwappers[pool] = false` and specific addresses are allowlisted. Any such pool that also wants to support the standard router path must allowlist the router, directly triggering the bypass. This is the expected operational configuration for a restricted pool that still serves users through the periphery. The trigger requires no privileged escalation beyond the pool admin's own intended setup.

---

### Recommendation

The extension must gate on the actual end user, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the real user through the pool**: Have the pool accept an explicit `swapper` parameter (separate from `msg.sender`) and forward it to extensions as `sender`. The router would pass `msg.sender` (the real user) in this field.
2. **Check inside the extension using extensionData**: Require the router to encode the real user's address in `extensionData` and have `SwapAllowlistExtension` decode and verify it, combined with a signature or trusted-forwarder pattern.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly supplied by the caller), not on `sender`.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `beforeSwap` order. `allowAllSwappers[pool] = false`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for any router-mediated swap to work.
3. Non-allowlisted user Bob calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. Router calls `pool.swap(bob, zeroForOne, ...)` — pool's `msg.sender` is the router.
5. Pool calls `_beforeSwap(sender=router, ...)` → extension checks `allowedSwapper[pool][router]` → `true`.
6. Bob's swap executes successfully despite not being on the allowlist.
7. Any number of non-allowlisted users can repeat step 3–6 indefinitely, draining LP-provided liquidity through unauthorized swaps. [4](#0-3) [5](#0-4) [2](#0-1)

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

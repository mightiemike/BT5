### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `swap` call — the router — not the originating EOA. When a pool admin allowlists the router to permit router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument the pool passes to `_beforeSwap`. `ExtensionCalling._beforeSwap` encodes that argument directly from the `sender` parameter it receives:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

The pool's `swap` function passes its own `msg.sender` as `sender` to `_beforeSwap`. When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the originating EOA:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
``` [3](#0-2) 

The allowlist check therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including users who were never individually allowlisted. The per-user curation is completely defeated.

The admin faces an inescapable dilemma:
- **Do not allowlist the router** → individually allowlisted users cannot use the standard periphery (broken core flow).
- **Allowlist the router** → any user bypasses the allowlist via the router (guard fails open).

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps on the pool, draining LP value through arbitrage or executing trades the pool admin explicitly intended to block. This is a direct bypass of an admin-configured access-control guard with fund-impacting consequences on production pools.

---

### Likelihood Explanation

The router is the canonical, documented periphery entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also wants users to be able to swap via the router must allowlist the router — the bypass is therefore a natural consequence of normal operational setup, not an exotic edge case. No privileged access, no special tokens, and no unusual preconditions are required beyond the router being allowlisted.

---

### Recommendation

Pass the originating user's address through the swap call so the extension can gate the correct actor. Two concrete options:

1. **Pool-level fix**: Have the pool record the original `msg.sender` in transient storage before calling the price provider and extensions, and expose it as a `swapInitiator()` view so extensions can read the real user.
2. **Extension-level fix**: Change `SwapAllowlistExtension.beforeSwap` to ignore the `sender` argument and instead require callers to supply the real user address inside `extensionData`, then verify it against the allowlist. The router would forward the user-supplied bytes unchanged.

The deposit-side extension (`DepositAllowlistExtension`) does not share this flaw because it gates `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`. [4](#0-3) 

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension 1)
  allowedSwapper[pool][alice] = true          // alice is individually allowlisted
  allowedSwapper[pool][router] = true         // router allowlisted so alice can use it
  bob = arbitrary EOA, NOT in allowedSwapper

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ..., recipient: bob})
  2. router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
     → pool's msg.sender = router
  3. pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. ExtensionCalling encodes beforeSwap(router, bob, ...)
  5. SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
  6. Swap executes for bob despite bob never being allowlisted.

Result: bob trades on a curated pool, bypassing the per-user allowlist entirely.
``` [1](#0-0) [5](#0-4) [6](#0-5)

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

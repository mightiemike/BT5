Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` (the router contract) as `sender` to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. The extension gates on `allowedSwapper[pool][router]` rather than the originating user, so allowlisting the router — operationally required for any allowlisted user to trade through the supported periphery — simultaneously opens the pool to every non-allowlisted address. Additionally, `SwapAllowlistExtension.beforeSwap` silently drops the `onlyPool` modifier present in the base class, allowing any address to invoke the hook directly.

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` and passes it as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` relays that value unchanged as the first positional argument of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` delivered to the extension is the router, not the originating user. The check becomes `allowedSwapper[pool][router]` — a single boolean that is either true for every caller or false for every caller. No per-user discrimination is possible through the router path.

The `onlyPool` modifier is present in the base class override: [5](#0-4) 

But is silently dropped in `SwapAllowlistExtension.beforeSwap`: [6](#0-5) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the router is allowlisted — which is operationally required for any allowlisted user to trade through the supported periphery — the restriction is completely nullified for all users. Any non-allowlisted address can execute swaps against the pool's full liquidity, receiving tokens out and paying tokens in, with no restriction. This constitutes a direct bypass of a core pool access-control mechanism with fund-impacting consequences: unauthorized parties trade against LP-owned assets without permission. [7](#0-6) 

## Likelihood Explanation
The bypass requires only that the pool admin has allowlisted the router, which is the expected operational configuration for any pool that intends to support allowlisted users trading through the canonical periphery. The attacker needs no special privilege, no flash loan, and no multi-transaction setup — a single call to any of the router's public swap functions suffices. The router is a publicly deployed periphery contract, so the attack surface is permanently reachable once the router is allowlisted. [8](#0-7) 

## Recommendation
The pool's `swap` function must expose the originating user's address to extensions independently of `msg.sender`. Two viable approaches:

1. **Add an explicit `originator` parameter to `pool.swap`** that the router populates with `msg.sender` (the actual user). The pool passes this through to `_beforeSwap` alongside the existing `sender`. The extension gates on `originator` when non-zero.

2. **Move allowlist enforcement into the router** so the router rejects non-allowlisted callers before calling the pool, and the pool-level extension is removed or made router-aware.

Additionally, restore the `onlyPool` modifier in `SwapAllowlistExtension.beforeSwap` so that only registered pools can invoke the hook:

```solidity
// fixed
function beforeSwap(...) external view override onlyPool returns (bytes4) { ... }
``` [5](#0-4) 

## Proof of Concept
```
Setup
─────
1. Pool admin deploys MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls SwapAllowlistExtension.setAllowedToSwap(pool, alice, true)
   — Alice is the only allowlisted swapper.
3. Pool admin calls SwapAllowlistExtension.setAllowedToSwap(pool, router, true)
   — Router is allowlisted so Alice can trade through the supported periphery.

Attack (Bob is not allowlisted)
────────────────────────────────
4. Bob calls MetricOmmSimpleRouter.exactInputSingle(...) targeting the pool.
5. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData).
   → Inside pool.swap: msg.sender == router
   → _beforeSwap(router, recipient, ...) is called.
6. SwapAllowlistExtension.beforeSwap receives sender == router.
   → Checks: allowedSwapper[pool][router] == true  ✓
   → Does NOT revert.
7. Swap executes. Bob receives output tokens. Allowlist is bypassed.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [9](#0-8) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

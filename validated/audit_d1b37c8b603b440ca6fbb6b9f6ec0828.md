All code paths are confirmed. The vulnerability is real and fully supported by the production code.

**Verification summary:**

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` verbatim to every configured extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is whoever called `pool.swap()` [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap()` directly, making the router `msg.sender` of the pool [4](#0-3) 
- `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the explicit position owner), not `sender`, so it is not affected [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates pool swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates the router's allowlist status rather than the actual end user's. Any user who calls through the router bypasses per-user swap gating on curated pools whenever the router itself is allowlisted.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap(), not the economic actor
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly, making `sender = router`. The extension then evaluates `allowedSwapper[pool][router]` — the router's allowlist status — rather than the actual user's. No mechanism exists in the router to forward the real user's address to the extension; `extensionData` is passed through as-is from the user's call, and the extension does not read it.

The `DepositAllowlistExtension` is not affected because it checks the `owner` parameter (the explicit position owner passed to `addLiquidity`), not `sender`.

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties. To allow those users to trade via the standard periphery path, the admin allowlists the router address. Once the router is allowlisted, any address — including explicitly non-allowlisted users — can call `MetricOmmSimpleRouter` and have their swap pass the extension check, because the extension sees `sender = router` (allowlisted) rather than the actual caller (not allowlisted). The curation policy is entirely defeated: non-permitted parties trade on what the admin believed was a restricted pool, breaking the core allowlist functionality of the extension.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the documented production entry point for swaps. Pool admins who configure `SwapAllowlistExtension` and also want router-based trading to work for their allowlisted users will naturally allowlist the router address — this is the only way to enable router-based trading for any user. This is the exact configuration that opens the bypass. No special permissions, flash loans, or exotic token behavior are required; a single public router call suffices. The bypass is repeatable and unconditional once the router is allowlisted.

## Recommendation
1. **Pass the real user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` (the actual end user) as part of `extensionData` or via a dedicated parameter so the extension can recover it. Alternatively, the pool's `swap` interface could accept an explicit `swapper` address distinct from `msg.sender`.
2. **Extension-side**: `SwapAllowlistExtension` should document that `sender` is the direct pool caller, and pool admins must not allowlist shared routers. A better long-term fix is for the extension to read the actual user from `extensionData` when a router is involved, or for the router to call the pool with the user's address forwarded explicitly.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   (admin enables router-based trading)
  - allowedSwapper[pool][alice] = true    (alice is a KYC'd user)
  - allowedSwapper[pool][bob]   = false   (bob is NOT allowlisted)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → swap proceeds ✓ (bob bypassed the allowlist)

Expected:
  SwapAllowlistExtension should check allowedSwapper[pool][bob] == false → revert
```

### Citations

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

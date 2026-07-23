Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool that allowlists the router to enable router-mediated swaps simultaneously opens the gate to every address on-chain, completely defeating the curation the extension was deployed to enforce.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` the pool sees. The original user's address is stored only in transient callback context for payment settlement and is never forwarded to the pool or extension: [4](#0-3) 

The same pattern applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. The extension always sees the router's address as `sender`, never the human trader's address.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position recipient explicitly passed by the caller), not `sender`, which is immune to this mismatch: [5](#0-4) 

This asymmetry confirms the flaw is specific to `SwapAllowlistExtension`, not a systemic design choice.

## Impact Explanation

Two direct fund-impacting outcomes:

1. **Allowlist bypass (critical path):** A pool admin who adds the router to the allowlist so that legitimate users can trade via the supported periphery path simultaneously grants every address on-chain the ability to swap. Any disallowed user routes through `MetricOmmSimpleRouter` and the extension passes because the router is on the allowlist. The curation boundary is completely erased, enabling unauthorized swaps against a curated pool that can drain LP principal or execute swaps the pool designer explicitly prohibited.

2. **Broken functionality for allowlisted users:** A pool admin who adds individual user addresses to the allowlist (the natural usage) blocks those same users from using the router, because the router's address is not on the allowlist. Legitimate users are locked out of the primary supported swap path.

Both outcomes constitute broken core pool functionality causing loss of funds or unusable swap flows, meeting the required impact gate.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entry point for end users. Any pool that deploys `SwapAllowlistExtension` and expects users to trade through the router will encounter this mismatch on the very first router-mediated swap. The trigger requires no special privileges, no malicious setup, and no non-standard tokens — only a public call to the router. The condition is met by default usage of the extension with the supported periphery.

## Recommendation

The extension must gate the economically relevant actor. Two options:

1. **Pass the original user through the extension payload (simplest correct fix):** Require the router to embed the originating user address in `extensionData`; the extension decodes and checks it instead of (or in addition to) `sender`. This ensures the allowlist always gates the human trader regardless of which periphery contract relays the call.

2. **Dual-check with router fallback:** The extension checks `allowedSwapper[pool][sender]` first; if `sender` is a known/registered router, it falls back to an address the router encoded in `extensionData`.

Option 1 is the simplest and most robust fix.

## Proof of Concept

```
Pool configured with SwapAllowlistExtension.
Pool admin calls:
  swapExtension.setAllowedToSwap(pool, address(router), true)
  // intending to allow router-mediated swaps for legitimate users

Attacker (not individually allowlisted) calls:
  router.exactInputSingle({pool: pool, ..., extensionData: ""})

Execution path:
  router.exactInputSingle
    → pool.swap(recipient, ..., extensionData)   // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ PASSES
      → swap executes, attacker receives output tokens

Result: attacker bypasses the swap allowlist and trades against a curated pool.
```

A Foundry integration test can confirm this by: (1) deploying a pool with `SwapAllowlistExtension`, (2) allowlisting only the router address, (3) calling `router.exactInputSingle` from an address not individually allowlisted, and (4) asserting the swap succeeds when it should revert.

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

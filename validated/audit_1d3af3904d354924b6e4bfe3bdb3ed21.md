Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Allowlist Bypass or Blocking Allowlisted Users' Router Access - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool — the immediate caller, which is the router contract when users go through `MetricOmmSimpleRouter`. This means any non-allowlisted user can bypass a curated pool's allowlist by routing through the router if the router is allowlisted, and symmetrically, allowlisted users are silently blocked from using the router if it is not allowlisted.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly with no originating-user field: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` delivered to the extension is the router, not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]` — the wrong actor entirely.

`DepositAllowlistExtension` avoids this by checking `owner` (the LP position beneficiary, an explicit parameter the caller provides), not `sender`: [5](#0-4) 

No equivalent correction exists in `SwapAllowlistExtension`. The `recipient` field is available in `beforeSwap`'s signature but is ignored by the extension.

## Impact Explanation

Two fund-impacting outcomes follow from the same root cause:

**Bypass path (High):** A pool admin allowlists the router as a trusted intermediary. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool. The extension sees `allowedSwapper[pool][router] == true` and permits the swap. The curation policy is completely defeated; disallowed users trade against LP assets they were never meant to access — a direct allowlist bypass enabling unauthorized trading against LP principal.

**Broken-access path (Medium):** The admin does not allowlist the router. Allowlisted users who call through the router have their swaps reverted (`NotAllowedToSwap`) even though they are individually permitted. The router — a core supported periphery path — is unusable on any allowlisted pool, breaking the expected swap flow for legitimate users.

## Likelihood Explanation

The trigger requires only a standard public call to `MetricOmmSimpleRouter.exactInputSingle` or any multi-hop `exactInput`/`exactOutput` path. No privileged access, no malicious setup, and no non-standard tokens are needed. Any user can reach the bypass path the moment the router is allowlisted, and any allowlisted user hits the broken-access path the moment they use the router on a curated pool. Both conditions are routine operational states.

## Recommendation

Replace the `sender` check with the originating user. The simplest fix consistent with the existing interface is to have the router pass the originating user as a verified field in `extensionData` and have the extension decode and verify it, or to redesign the `sender` argument so the pool always propagates the economic actor rather than the immediate caller. As a parallel fix, align `SwapAllowlistExtension` with `DepositAllowlistExtension`'s pattern: gate on the economically relevant actor (e.g., `recipient`, or an attested originator passed via `extensionData`), not on the intermediary contract that called the pool.

## Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // Alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)      // Router allowlisted as trusted intermediary

Bypass (Bob is NOT allowlisted)
────────────────────────────────
4. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
   → msg.sender inside pool = router
6. Pool calls _beforeSwap(router, bob, ...)
7. SwapAllowlistExtension.beforeSwap receives sender = router
8. Check: allowedSwapper[pool][router] == true  → PASSES
9. Bob's swap executes. Allowlist bypassed.

Broken access (Alice IS allowlisted, router is NOT)
────────────────────────────────────────────────────
4. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(alice_recipient, ...)
   → msg.sender inside pool = router
6. Pool calls _beforeSwap(router, alice_recipient, ...)
7. SwapAllowlistExtension.beforeSwap receives sender = router
8. Check: allowedSwapper[pool][router] == false → REVERTS NotAllowedToSwap
9. Alice cannot use the router despite being individually allowlisted.
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

Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the real swapper, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. Any pool admin who allowlists the router to enable router-based trading simultaneously grants every on-chain address access to the pool, completely defeating the per-user access control the extension is designed to enforce.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the pool's `msg.sender` the router contract: [4](#0-3) 

The same substitution occurs in `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

At that point, `sender` forwarded to the extension is the router address, not the end-user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. There is no mechanism in the extension or the pool to recover the originating user identity. The `extensionData` field passed by the router is always `""` (empty string) for `exactInputSingle`, providing no user identity either.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` intends to restrict trading to a specific set of counterparties (e.g., KYC-verified addresses, institutional partners). To allow those counterparties to use the standard router, the admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, every address on-chain can call `MetricOmmSimpleRouter.exactInputSingle` and reach the pool, because the extension only sees the router address and approves it. The per-user allowlist is completely bypassed. Unauthorized swappers can trade against the pool at oracle-derived bid/ask prices, extracting value from LP positions. This constitutes broken core pool functionality — the access-control invariant the extension is designed to enforce is silently voided, resulting in direct loss of LP principal.

## Likelihood Explanation
Medium-High. The router is the canonical user-facing entry point. Any pool admin who wants allowlisted users to trade through the UI/router will naturally allowlist the router address. The bypass requires no privileged access, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle` or `exactInput` function. The admin's own correct operational step (allowlisting the router) is what opens the hole.

## Recommendation
The extension must check the originating user, not the immediate caller of `pool.swap`. Two options:

1. **Pass the real user through the router.** Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value. This requires a trust assumption that only the legitimate router populates this field, which can be enforced by checking `sender == trustedRouter` before accepting the decoded identity.

2. **Use transient storage for user identity.** Have the router write the real user into a transient storage slot before calling `swap`, analogous to how the router already uses transient storage for callback context (`_setNextCallbackContext`). The extension or pool can then read this slot to recover the originating user.

Either way, the extension must gate the economically relevant actor — the address whose funds are being used — not the contract that happens to be the immediate `msg.sender` of `pool.swap`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router users
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
  - Swap executes; attacker receives output tokens

Result:
  - attacker swapped against the pool despite never being allowlisted
  - isAllowedToSwap(pool, attacker) returns false, yet the swap succeeded
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

Audit Report

## Title
Swap Allowlist Extension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Curated Pool Access Control — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates access by checking the `sender` argument, which is `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract address, not the end user. Because the router is a permissionless public contract, adding it to the allowlist (the only way to restore router functionality) grants every user on the internet access to a curated pool, completely defeating the per-user access control.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the address passed by the pool: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router itself `msg.sender` at the pool: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. There is no mechanism in the router to forward the real caller's identity to the extension — `extensionData` is passed through from `params.extensionData` without any attestation of `msg.sender`. [4](#0-3) 

Two failure modes result:
- Router **not** in allowlist: all router-mediated swaps revert, breaking the canonical periphery path for every user including legitimate ones.
- Router **in** allowlist (the only fix): every address can call `exactInputSingle` targeting the curated pool and succeed, because the extension sees only the router address and approves it.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). The bypass allows any unprivileged address to trade on such a pool by routing through `MetricOmmSimpleRouter`, directly violating the pool admin's access-control intent. Trades that should be blocked execute at live oracle prices against LP funds, with the allowlist providing zero protection once the router is allowlisted. This is a direct loss of LP principal to unintended counterparties. **Impact: High.**

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps.
- No special setup is required: any EOA can call `exactInputSingle` with the target pool address.
- The bypass is deterministic and requires no oracle manipulation, flash loans, or privileged access.
- The only precondition is that the pool admin has configured `SwapAllowlistExtension` and added the router to the allowlist to restore router functionality (a natural operational step).

**Likelihood: High.**

## Recommendation

The extension must identify the economic actor, not the immediate caller. The preferred fix is for the router to encode `msg.sender` into `extensionData` and for the extension to decode and verify it, with the extension rejecting calls that do not carry a valid attestation from a trusted router registry. Alternatively, redesign the allowlist to key on `recipient` where the recipient is constrained to equal the caller (enforced in the router). Using `tx.origin` is not recommended for composability reasons.

## Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
   Router address is NOT in the allowlist.
3. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
     pool=targetPool, recipient=bob, zeroForOne=true, amountIn=X, extensionData=""
   ).
4. Router calls pool.swap(...) — msg.sender at pool = router address.
5. SwapAllowlistExtension.beforeSwap receives sender=router.
6. allowedSwapper[pool][router] == false → revert. (Router path broken for all users.)

Pool admin adds router to allowlist to restore router functionality:
7. setAllowedToSwap(pool, router, true).
8. Bob repeats step 3 → swap succeeds.
   Alice's exclusive access is gone; any address can trade through the router.

Expected: Bob's swap should revert because Bob is not allowlisted.
Actual: Bob's swap succeeds because the extension checks the router address, not Bob.
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

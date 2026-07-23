Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the originating user, allowing full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `MetricOmmPool.swap()` unconditionally passes its own `msg.sender` as `sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract. If the pool admin allowlists the router address — a natural step to enable router-mediated swaps — every unprivileged user can bypass the allowlist by routing through the router.

## Finding Description
**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

When the router calls `pool.swap()`, `msg.sender` inside the pool is the router contract address, not the originating EOA.

**Step 2 — Router calls `pool.swap()` directly with no originating-caller forwarding.**

`exactInputSingle` calls `pool.swap(params.recipient, ...)` with no mechanism to encode or forward the originating EOA: [2](#0-1) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [3](#0-2) 

**Step 3 — Extension checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap` uses `sender` (the first argument, which is the router) as the identity to gate: [4](#0-3) 

Here `msg.sender` is the pool (correct — used as the mapping key) and `sender` is the router address. The lookup resolves to `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any user routing through the router is checked as if they were the router itself.

**Two failure modes:**

| Scenario | Effect |
|---|---|
| Admin allowlists the router | Every user bypasses the allowlist by routing through the router |
| Admin does not allowlist the router | Every individually allowlisted user is blocked when using the router |

The bypass is the fund-impacting mode. Allowlisting the router is the natural operational step for a pool admin who wants their KYC'd users to trade via the standard periphery.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, whitelisted market makers) loses that restriction entirely for any user routing through `MetricOmmSimpleRouter`. Unauthorized users can trade against the pool's LP positions, extracting value through oracle-priced swaps the pool admin intended to restrict. This is a direct loss of LP principal and a complete bypass of the pool's access-control invariant — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break by an unprivileged path" allowed impacts.

## Likelihood Explanation
The bypass requires the admin to have allowlisted the router address. This is a natural and expected operational step: a pool admin who wants their allowlisted users to use the standard router would call `setAllowedToSwap(pool, address(router), true)`. The `isAllowedToSwap` view function and the admin setter both operate on individual addresses with no indication that the router is a shared identity. Once the router is allowlisted, the bypass is reachable by any unprivileged user with no special setup, no privileged role, and no non-standard token behavior.

## Recommendation
The extension must gate the originating user, not the immediate caller of `pool.swap()`. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before calling the pool, and have the extension decode and check it — with a trusted encoding convention enforced by the router. Alternatively, add an explicit `senderOverride` field to the swap interface that the router populates with its own `msg.sender` before calling the pool, making the originating-caller identity contract-explicit. A third option is to document that the allowlist gates only the direct pool caller and require curated pools to deploy a custom router that enforces per-user checks before calling the pool, removing the standard router from the allowlist entirely.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin allowlists the router (intending to let KYC'd users trade via router):
       swapExtension.setAllowedToSwap(pool, address(router), true);
3. Non-allowlisted EOA (attacker) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: curatedPool,
           recipient: attacker,
           ...
       });
4. Router calls pool.swap(attacker, ...) — pool sees msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
7. Swap executes. Attacker trades on the curated pool without being individually allowlisted.
```
The attacker never appears in `allowedSwapper[pool][attacker]`, yet the swap succeeds because the extension checked the router's address instead. A Foundry integration test can reproduce this by deploying the pool with the extension, calling `setAllowedToSwap(pool, router, true)`, then calling `router.exactInputSingle` from an address not in the allowlist and asserting the swap succeeds.

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

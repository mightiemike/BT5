Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, `sender` is the router address, not the actual end user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every user — including non-allowlisted ones — can bypass the individual allowlist by routing through the router, silently defeating the pool's curation policy.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at line 230. When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` is the router contract address.

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` verbatim.**
`ExtensionCalling._beforeSwap` (lines 149–177) encodes `sender` directly into the `abi.encodeCall` for `IMetricOmmExtensions.beforeSwap` without any transformation.

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the router.**
The check at line 37 resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Step 4 — The router never forwards the real user identity to the pool or extension.**
`MetricOmmSimpleRouter.exactInputSingle` (lines 71–80) stores `msg.sender` only in transient callback context (`_setNextCallbackContext`) for payment purposes. It passes `params.extensionData` (caller-controlled) and `""` as `callbackData` to `pool.swap()`. The actual caller is never passed to the extension.

**The dilemma this creates:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user bypasses the individual allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

## Impact Explanation
A pool deployer who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or protocol-owned addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool's curation policy is silently defeated. Unauthorized users can drain liquidity, extract favorable oracle-priced trades, or front-run allowlisted users on a pool designed to be closed. This constitutes broken core pool functionality — the allowlist extension is the only mechanism for restricting swap access, and it fails open for the standard periphery path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the documented standard swap entry point. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address — this is a natural, expected operational step, not an exotic configuration. The bypass is therefore reachable on any curated pool that supports router-mediated swaps. No special attacker capability is required beyond calling `exactInputSingle` on the router.

## Recommendation
The extension must check the actual user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Trusted router registry**: The extension maintains a registry of trusted routers; when `sender` is a known router, it decodes the real user from `extensionData` and checks that address instead.
3. **Periphery-layer enforcement**: Require all swaps to go through the router and have the router enforce the allowlist before calling the pool, moving the gate to where the real user is known.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowlisted for alice to use it
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ..., extensionData)   // msg.sender = router
6. Pool calls _beforeSwap(msg.sender=router, ...)
7. Extension checks allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes on the curated pool, bypassing the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

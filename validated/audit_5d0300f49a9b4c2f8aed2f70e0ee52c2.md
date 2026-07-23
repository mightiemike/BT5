Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates pool swaps by checking the `sender` parameter against a per-pool allowlist, but `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router's address is substituted for the actual user's address. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously opens the gate to every user, completely defeating the allowlist.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to every extension hook.**

`MetricOmmPool.swap()` unconditionally forwards its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` value and nothing else.**

`beforeSwap` uses `msg.sender` as the pool key (correct) and `sender` as the swapper identity (wrong when routed): [2](#0-1) 

**Step 3 — The router is the direct caller of `pool.swap()`, not the end-user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making `pool.msg.sender = router`. The actual user's identity (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the extension: [3](#0-2) 

**Step 4 — The admin faces an impossible choice.**

| Admin intent | Admin action | Consequence |
|---|---|---|
| Allow allowlisted users to use the router | `setAllowedToSwap(pool, router, true)` | Every user bypasses the allowlist via the router |
| Block non-allowlisted users from the router | Do not allowlist the router | Allowlisted users cannot use the router at all |

No configuration simultaneously allows router-mediated swaps for approved users and blocks unapproved users. The actual user identity is never validated by the extension.

## Impact Explanation
Any user can swap in a pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. Once the router is allowlisted (the only way to let legitimate users use the router), the allowlist provides zero protection. Unauthorized users can drain liquidity, execute arbitrage, or trade in pools intended to be restricted (e.g., KYC-gated, institutional-only, or compliance-restricted pools). This is a direct loss of the access-control invariant with fund-impacting consequences for LP holders in restricted pools. Severity: **High** — broken core access control with direct fund impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension` and also wants legitimate users to access the router will naturally allowlist the router. The bypass is reachable in any realistic production deployment of this extension. No special privileges or malicious setup are required — a standard public call to `exactInputSingle` is sufficient.

## Recommendation
The extension must gate the actual end-user, not the intermediary router. Two viable approaches:

1. **Propagate the real payer through `extensionData`**: Have the router ABI-encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires the extension to trust that the router is the only allowlisted intermediary and that it faithfully encodes the real caller — a fragile assumption.
2. **Dedicated attested sender field**: Add a dedicated "attested sender" field to the extension interface or use a signed permit pattern so the extension can verify the real user regardless of the call path.

The cleanest fix is to never allowlist the router as a swapper; instead, redesign the router to forward a cryptographically attested user identity that the extension can verify independently.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension attached.
2. Admin: setAllowedToSwap(pool, userA, true)       // legitimate user
3. Admin: setAllowedToSwap(pool, router, true)       // needed so userA can use the router
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: userB, ...})
5. router calls pool.swap(userB, ...)
6. pool calls extension.beforeSwap(sender=router, ...)
7. allowedSwapper[pool][router] == true  →  check passes
8. userB's swap executes successfully — allowlist fully bypassed.
```

No privileged access, no malicious setup, no non-standard tokens required. A single public router call is sufficient.

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

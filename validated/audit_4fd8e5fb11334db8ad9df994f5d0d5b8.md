Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension` gates pool swaps to a per-pool allowlist by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router — the natural action to let allowlisted users access the standard swap interface — every unprivileged caller can bypass the per-user gate entirely by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

So the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. If the router is allowlisted, the check passes for any caller regardless of their individual allowlist status. There is no existing guard that recovers the true end user identity — `extensionData` is passed through but the extension does not decode it.

## Impact Explanation
Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool whose `SwapAllowlistExtension` has the router allowlisted. The extension sees `sender = router` and passes the check regardless of the end user's identity. On pools designed to restrict trading to specific counterparties (KYC-gated, market-maker-only, compliance-restricted), this allows arbitrary users to trade, defeating the access control entirely for all router-mediated swaps. This constitutes a broken core pool access-control invariant with direct potential for fund loss or unauthorized trading.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router address. This is the natural and expected configuration: the router is the protocol's standard swap interface, and pool admins who want their allowlisted users to trade through it will allowlist it. The admin has no indication from the extension's interface or documentation that doing so opens the gate to all users. The condition is therefore a reasonable admin action, not a malicious or unusual one, making this a medium-likelihood finding.

## Recommendation
The extension must gate on the economically relevant actor, not the intermediary. The cleanest fix is to require the end user to pass their identity in `extensionData` and verify it with a signature or on-chain proof: the extension decodes the end user address from `extensionData`, checks `allowedSwapper[pool][endUser]`, and the router forwards the correct `extensionData` per hop. Alternatively, document explicitly that the router must never be allowlisted and that allowlisted users must call the pool directly — but this breaks the standard UX and is not a code-level fix.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   (alice is the intended gated user)
  allowedSwapper[pool][router] = true   (admin adds this so alice can use the router)

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Flow:
    router → pool.swap(recipient=bob, ..., extensionData)
      pool: sender = router (msg.sender of pool.swap)
      pool: _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  → passes
      swap executes for bob

Result: bob, who is not in the allowlist, successfully swaps on the curated pool.
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

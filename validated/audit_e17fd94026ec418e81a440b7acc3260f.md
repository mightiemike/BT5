Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any user to bypass per-user swap restrictions when the router is allowlisted — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router address, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user on the network can bypass the per-user restriction by routing through the router. There is no configuration that permits specific users to swap via the router while blocking others.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the extension call without modification: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][directCaller]`. The `bytes calldata` extensionData parameter is unnamed and entirely ignored: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` in the pool context: [4](#0-3) 

The same pattern applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. When the router calls `pool.swap()`, `sender` = router address. The extension has no visibility into which end user initiated the router call. Because `extensionData` is ignored, there is no fallback path to recover the original user identity.

This creates an all-or-nothing split:

| Admin configuration | Direct pool call | Router-mediated call |
|---|---|---|
| Router NOT allowlisted | Allowlisted users pass | Everyone blocked, including allowlisted users |
| Router allowlisted | Allowlisted users pass | **Everyone passes**, including non-allowlisted users |

There is no configuration that allows specific users to swap through the router while blocking others.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific market makers or KYC-verified addresses is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. An unauthorized user can execute swaps against the pool's LP liquidity at oracle-anchored prices, draining one side of the pool's bins and causing direct loss of LP principal. The allowlist guard — the only mechanism protecting LP funds from unauthorized swap flow — is rendered ineffective for all router-mediated paths. This constitutes broken core pool functionality causing direct loss of LP funds, meeting the High/Medium threshold.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router, which is a routine and expected operational step: without it, even allowlisted users cannot use the router. Any user who observes that the router is allowlisted (readable on-chain via `allowedSwapper[pool][router]`) can immediately exploit the bypass with no further preconditions. The trigger is fully unprivileged and requires no special capability beyond calling the public router.

## Recommendation
The extension must recover the original end-user identity rather than relying on `sender` (the direct pool caller). Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have `MetricOmmSimpleRouter` ABI-encode the original `msg.sender` into `extensionData` before calling `pool.swap()`. Update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router address.

2. **Check both `sender` and decoded origin**: If `sender` is a registered router, decode the real user from `extensionData` and apply the allowlist check against that address instead.

Either approach must be paired with a registry of trusted routers so the extension cannot be tricked by a malicious contract that fabricates a user address in `extensionData`.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin calls setAllowedToSwap(pool, mm1, true)       // allowlist market maker
  admin calls setAllowedToSwap(pool, router, true)    // allow router-mediated swaps for mm1

Attack:
  attacker (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle(pool, ...)

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, ...)          // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ PASSES
      → swap executes, LP funds transferred to attacker

Result:
  Non-allowlisted attacker completes swap.
  allowedSwapper[pool][attacker] == false was never checked.
  The per-user allowlist is fully bypassed.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension
  2. allowlist mm1 and router
  3. Call router.exactInputSingle from attacker address
  4. Assert swap succeeds and attacker receives output tokens
  5. Assert allowedSwapper[pool][attacker] == false (was never checked)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

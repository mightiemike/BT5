Audit Report

## Title
SwapAllowlistExtension gates router address instead of actual swapper, enabling full allowlist bypass via router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter — the direct caller of `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every unprivileged user the ability to bypass the individual allowlist entirely, collapsing the curation invariant for all router paths.

## Finding Description

**Root cause — wrong actor bound in the allowlist check.**

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, **not** `allowedSwapper[pool][actualUser]`. The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181).

**The resulting dilemma for pool admins:**

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | Every user, including non-allowlisted ones, bypasses the allowlist via the router |

No configuration simultaneously allows router-mediated swaps and enforces per-user allowlist restrictions. The exact wrong value is `sender = router address` instead of `sender = actual end user`, corrupting the extension's identity binding for every router-mediated swap.

## Impact Explanation
A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` and allowlists the router exposes the pool to unrestricted swapping by any address. Any non-allowlisted user can call `exactInputSingle` or `exactInput` and the extension passes the check because `allowedSwapper[pool][router] = true`. The curation invariant is fully broken: the pool behaves as if `allowAllSwappers[pool] = true` for all router users. This constitutes broken core pool functionality — the access-control extension produces no meaningful restriction — and represents a direct bypass of an admin-configured security boundary by an unprivileged caller.

## Likelihood Explanation
The trigger is unprivileged: any user can call the public router functions. The only precondition is that the pool admin has allowlisted the router address, which is the natural and expected step when deploying a curated pool intended to be usable through the standard periphery. The admin is not malicious — they are unaware that allowlisting the router collapses per-user enforcement for all router paths. This configuration is likely in any production deployment that intends to support the router alongside access control.

## Recommendation
Pass the original end-user address through the call chain rather than the immediate `msg.sender`. One approach: have the router encode the originating `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when present. Alternatively, the pool could accept an explicit `originator` parameter distinct from `sender`, or the extension could be redesigned to check both the direct caller and a router-attested originator. The core fix is ensuring the gated identity is the economic actor initiating the swap, not the intermediary contract relaying it.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router usage; does **not** allowlist `attacker`.
3. `attacker` (non-allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. Router calls `pool.swap(...)` — pool passes `msg.sender = router` as `sender` to `_beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker successfully swaps despite never being individually allowlisted.

Foundry test outline:
```solidity
function test_allowlistBypass() public {
    // setup: pool with SwapAllowlistExtension, router allowlisted, attacker not allowlisted
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

    // attacker (not allowlisted) swaps via router — should revert but does not
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({pool: address(pool), ...}));
    // assert swap succeeded — demonstrates bypass
}
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

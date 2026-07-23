Audit Report

## Title
`SwapAllowlistExtension` allowlist bypass via router intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address to support router-mediated swaps on a curated pool inadvertently opens the gate to every user who routes through the router, completely bypassing the per-address allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the mapping key) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The same pattern applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181). In every router-mediated path the pool sees the router address as `sender`.

The check `allowedSwapper[pool][router] == true` evaluates to `true` for every caller who routes through the router, regardless of whether that caller is individually permitted. There is no mechanism in the extension to recover the originating user address — `extensionData` is passed through but the extension does not parse it for a delegated identity.

The existing test `test_allowedSwapSucceeds` allowlists `callers[0]` (a `TestCaller` contract that calls `pool.swap()` directly), not the router, so the bypass is not exercised: [5](#0-4) 

## Impact Explanation
Curated pools deploy `SwapAllowlistExtension` to restrict trading to a defined set of addresses (e.g., KYC-verified counterparties, institutional participants). A complete bypass of this guard allows any unpermissioned address to execute swaps against the pool, violating the pool's access-control invariant. LPs who deposited under the assumption that only allowlisted counterparties could trade are exposed to unauthorized traders. This constitutes broken core pool functionality with direct fund-impact consequences — the exact corrupted value is `allowedSwapper[pool][sender]` returning `true` for an address that was never individually permitted.

## Likelihood Explanation
Supporting the router is the standard user-facing path for the protocol. A pool admin who configures a curated pool and also wants users to access it through the official router will naturally call `setAllowedToSwap(pool, router, true)`. There is no documentation or on-chain signal warning that this single allowlist entry opens the gate to all users. The admin action is valid, follows the expected integration pattern, and requires no attacker privilege beyond being able to call the public router.

## Recommendation
The `sender` forwarded to extension hooks must represent the economic actor (the end user), not the intermediary contract. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode `msg.sender` (the originating caller) into `extensionData` so extensions can recover it. The router already stores the payer in transient storage; the same address should be surfaced to extensions.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should parse an optional `extensionData` payload carrying the verified end-user address when the direct `sender` is a known trusted router, falling back to `sender` for direct calls. This requires a trust registry for which routers are permitted to assert a delegated identity.

The cleanest fix is for `MetricOmmPool.swap()` to accept an explicit `onBehalfOf` parameter that extensions receive as the authoritative actor, with `msg.sender` still used for callback settlement.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap extension
  - Admin allowlists the router: setAllowedToSwap(pool, router, true)
  - Alice (address not in allowlist) wants to swap

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; Alice receives output tokens

Result:
  Alice, who is not individually allowlisted, successfully swaps on a
  curated pool. The allowlist guard is completely bypassed.

Foundry test: Add a test to FullMetricExtension.t.sol that:
  1. Deploys MetricOmmSimpleRouter
  2. Calls swapExtension.setAllowedToSwap(pool, router, true)
  3. Has an address NOT in the allowlist call router.exactInputSingle(...)
  4. Asserts the swap succeeds (demonstrating the bypass)
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

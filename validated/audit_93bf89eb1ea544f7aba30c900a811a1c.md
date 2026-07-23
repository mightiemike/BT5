Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router (required for any approved user to use the periphery) inadvertently opens the gate to every user, including non-allowlisted ones.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user identity forwarded: [4](#0-3) 

At that call site, `msg.sender` of `pool.swap()` is the router contract, so the extension evaluates `allowedSwapper[pool][router]`. Once the router is allowlisted (a necessary step for any approved user to use the periphery), the check degenerates to a constant `true` for every caller of the router, regardless of their individual allowlist status. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

`DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position owner explicitly passed by the caller), not `sender`: [5](#0-4) 

## Impact Explanation
A pool admin who deploys a restricted pool (KYC-gated, institutional-only, compliance-restricted) and configures `SwapAllowlistExtension` must allowlist the router for any approved user to use the standard periphery. Once the router is allowlisted, every user who calls through it can swap freely in the restricted pool. Non-allowlisted users can drain LP funds at oracle-anchored prices that LPs deposited under the assumption of a restricted counterparty set. This is a direct loss of LP principal and a broken admin-boundary invariant.

## Likelihood Explanation
The precondition — the router being allowlisted — is a natural and expected configuration for any restricted pool where approved users are meant to use the standard periphery. The router is a public, permissionless contract with no access control of its own. No privileged action, malicious setup, or non-standard token is required beyond the admin performing the expected allowlist configuration. The exploit is directly reachable by any unprivileged user and is repeatable indefinitely.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Forward the original user through the router**: `MetricOmmSimpleRouter` should pass `msg.sender` (the end user) as part of `extensionData` or a dedicated field, and `SwapAllowlistExtension` should decode and check that identity.
2. **Trusted-router registry**: The extension could maintain a registry of trusted routers and, when `sender` is a known router, require the router to attest the real user identity in `extensionData`.

The simplest safe fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it when `sender` is a known router address.

## Proof of Concept
1. Admin deploys pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — `msg.sender` of `pool.swap()` is the router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in a pool he is not authorized to access.

Foundry test outline:
```solidity
function test_routerBypassesSwapAllowlist() public {
    // Deploy pool with SwapAllowlistExtension
    // setAllowedToSwap(pool, alice, true)
    // setAllowedToSwap(pool, address(router), true)
    // vm.prank(bob); // bob is NOT allowlisted
    // router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
    // assert swap succeeds — bob bypassed the allowlist
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

Audit Report

## Title
`SwapAllowlistExtension` checks the immediate `pool.swap` caller (router) instead of the originating user, making the allowlist bypassable via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the router contract, not the originating EOA. The extension therefore checks whether the router is allowlisted rather than whether the user is allowlisted. This creates an irreconcilable dilemma: allowlisting the router to permit router-mediated swaps simultaneously grants every unpermissioned user bypass access; not allowlisting the router silently blocks every individually-allowlisted user from using the standard periphery.

## Finding Description

**Exact call chain:**

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the encoded extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap` — the router, not the originating user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the originating user's identity into the `sender` slot: [4](#0-3) 

The router stores `msg.sender` only in transient callback context (for payment purposes), never in a field the pool or extension can read: [5](#0-4) 

No existing guard in `SwapAllowlistExtension` inspects `extensionData` or any other field to recover the originating user. The wrong value checked is `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originatingUser]`.

## Impact Explanation

**Scenario A — allowlist bypass (High):** Pool admin allowlists the router so that allowlisted users can swap via the standard periphery. Because the extension checks the router address, every unpermissioned user can now swap by routing through `MetricOmmSimpleRouter`. The allowlist is completely ineffective. Any user can access liquidity from a pool intended to be KYC-gated or institutional-only, causing direct loss of LP principal through unauthorized access to privileged liquidity.

**Scenario B — broken core swap functionality (High):** Pool admin does not allowlist the router. Every individually-allowlisted user who calls `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` is silently blocked with `NotAllowedToSwap`, even though they are explicitly permitted. The standard periphery is entirely unusable for any allowlist-protected pool, breaking the core swap flow for legitimate users.

Both outcomes meet the allowed impact gate: Scenario A is a direct allowlist bypass with fund-impacting consequences; Scenario B is broken core pool functionality.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router immediately encounters one of the two failure modes. No special privilege is required — any public user can call the router. The pool admin's only apparent "fix" (allowlisting the router) is itself the bypass vector, making the vulnerability self-reinforcing.

## Recommendation

The extension must check the economically relevant actor, not the immediate `pool.swap` caller. Two viable options:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` (the originating user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between router and extension but requires no core changes.

2. **Structural fix:** Add an `originSender` field to the `beforeSwap` hook signature that the pool populates from a trusted transient-storage slot set by the router before calling `pool.swap`, analogous to how Uniswap v4 passes `msgSender` through the unlock mechanism. This is the most robust solution.

## Proof of Concept

```solidity
// Setup:
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
// 2. Pool admin allowlists alice but NOT bob.
// 3. Pool admin allowlists the router so alice can use it.
// 4. Bob (not allowlisted) calls router.exactInputSingle targeting the pool.
// 5. Extension sees sender = router (allowlisted) → swap succeeds for bob.

function test_swapAllowlist_bypass_via_router() public {
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    // admin allowlists router so alice can use it
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    vm.startPrank(bob);
    token0.approve(address(router), type(uint256).max);
    // Expect: should revert NotAllowedToSwap — but it SUCCEEDS
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            recipient: bob,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    vm.stopPrank();
    // bob received token1 despite not being allowlisted
}
```

The root cause is confirmed at: [6](#0-5) [7](#0-6) [4](#0-3)

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

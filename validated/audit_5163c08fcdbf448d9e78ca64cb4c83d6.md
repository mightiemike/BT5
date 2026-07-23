Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `beforeSwap` extension hook. When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` is the router contract, not the originating EOA. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `sender` is the router — so allowlisting the router to support normal UX inadvertently grants unrestricted swap access to every user on the network.

## Finding Description

**Root cause — identity substitution in the swap path**

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of `pool.swap()`) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165) — in every case the router is the direct caller of `pool.swap()`. [5](#0-4) 

**The forced admin dilemma**

A pool admin who wants allowlisted users to trade through the router must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router]` returns `true` for every swap arriving through the router, regardless of who initiated it. The `extensionData` bytes are forwarded to the extension but `SwapAllowlistExtension.beforeSwap` never reads them (the last parameter is unnamed and ignored): [6](#0-5) 

There is no mechanism to recover the originating user's identity. The admin cannot simultaneously (a) allow allowlisted users to use the router and (b) block non-allowlisted users from using the router.

**Existing guards are insufficient**

The `allowAllSwappers` flag is the only alternative, but it removes all per-user gating entirely. There is no trusted-forwarder pattern, no `extensionData` decoding, and no secondary identity check in the extension. [7](#0-6) 

## Impact Explanation

Any pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (KYC'd users, institutional partners, whitelisted bots) and also allowlisting the router loses its access control entirely for the router path. Non-allowlisted users can execute full swaps against the pool's LP liquidity. If the restriction existed to prevent adversarial flow (informed traders, sandwich bots, regulatory non-compliant counterparties), those actors can freely drain the pool's liquidity. This constitutes broken core pool functionality / admin-boundary break with direct LP exposure, matching the required impact gate.

## Likelihood Explanation

The trigger is a non-malicious, operationally expected admin action: allowlisting the router so that allowlisted users can trade through the standard periphery. Any pool operator who concludes "I need to allowlist the router for my users" will unknowingly open the gate. The router is a public, permissionless contract; once allowlisted, every user on the network can exploit the bypass in the same transaction. No front-running, flash loans, or special privileges are required.

## Recommendation

The router should encode the originating user's address into `extensionData` so that the extension can verify the real swapper. Alternatively, `SwapAllowlistExtension` should accept an optional `trustedForwarder` list: when `sender` is a known forwarder, the extension decodes the real user from `extensionData` and checks that address instead. A simpler short-term fix is to document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and that per-user gating is only enforceable on direct pool calls.

## Proof of Concept

```solidity
// Setup: pool guarded by SwapAllowlistExtension
// Admin allowlists alice (intended user) and the router (to let alice use the router)
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) routes through the router
vm.startPrank(bob);
token1.approve(address(router), type(uint256).max);

// pool.swap() is called with msg.sender = router
// extension checks allowedSwapper[pool][router] == true  ✓
// bob's swap executes successfully despite not being on the allowlist
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token1),
        tokenOut:        address(token0),
        zeroForOne:      false,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        recipient:       bob,
        deadline:        block.timestamp + 1,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    })
);
// bob receives token0 from the restricted pool — allowlist fully bypassed
vm.stopPrank();
```

The wrong value is `allowedSwapper[pool][router]` returning `true` for bob's swap at `SwapAllowlistExtension.beforeSwap` line 37, when the correct check should be against bob's address. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on `MetricOmmSimpleRouter`.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

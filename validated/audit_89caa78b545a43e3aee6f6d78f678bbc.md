Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any non-allowlisted user to bypass the per-trader swap gate via an allowlisted router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router address**, not the original user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any non-allowlisted user who routes through an allowlisted router bypasses the per-trader gate entirely, receiving pool output tokens without authorization.

## Finding Description

**Step 1 — Router calls pool directly:**

In `MetricOmmSimpleRouter.exactInputSingle`, the original caller (`msg.sender = attacker`) is stored only in the transient callback context for payment; it is never forwarded to the pool as the swap initiator. [1](#0-0) 

**Step 2 — Pool passes `msg.sender` (router) as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`. At this point `msg.sender` is the **router**, not the attacker. [2](#0-1) 

**Step 3 — Extension receives router address as `sender`:**

`ExtensionCalling._beforeSwap` encodes and dispatches `sender = router_address` to the extension via `abi.encodeCall`. [3](#0-2) 

**Step 4 — Extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][attacker]`:**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the pool key and `sender` (the router) as the swapper key. If the router is allowlisted, the check passes for **every** user who routes through it. [4](#0-3) 

The `allowedSwapper` mapping is designed to gate individual swapper addresses, but the address recorded is always the router when the router is used: [5](#0-4) 

## Impact Explanation

Any non-allowlisted user can swap against a pool that uses `SwapAllowlistExtension` with individual allowlisting, as long as the router contract itself is allowlisted. The attacker receives output tokens from the pool despite never being in `allowedSwapper[pool][attacker]`. This is a direct loss of pool output tokens to unauthorized parties and a complete break of the core access-control invariant the extension is designed to enforce. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" criteria.

## Likelihood Explanation

The scenario is highly likely in practice: pool admins who want to support router-based swaps for their allowlisted users will naturally add the router to `allowedSwapper`. Once the router is allowlisted, the gate is open to everyone. No special permissions, no malicious setup, and no non-standard token behavior are required — only a standard `exactInputSingle` call from any EOA.

## Recommendation

The extension must check the **originating user**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the pool admin to trust that only the legitimate router populates this field (enforce via a router-address allowlist in the extension).
2. **Preferred — remove router-level allowlisting; require users to call the pool directly** for allowlisted pools, or add a dedicated `originalSender` field to the extension hook interface so the pool can propagate the true initiator.

## Proof of Concept

```solidity
// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Pool admin allowlists the router:
//    swapExt.setAllowedToSwap(address(pool), address(router), true);
//    (attacker is NOT allowlisted)
// 3. Attacker calls:

vm.startPrank(attacker);
token1.approve(address(router), type(uint256).max);

uint256 amountOut = router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        tokenOut: address(token0),
        zeroForOne: false,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: type(uint128).max,
        extensionData: ""
    })
);
vm.stopPrank();

// Assertions:
assertGt(amountOut, 0, "attacker received output despite not being allowlisted");
assertFalse(swapExt.isAllowedToSwap(address(pool), attacker), "attacker is not in allowlist");
// The swap succeeded because allowedSwapper[pool][router] == true,
// and sender passed to beforeSwap was the router, not the attacker.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-12)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
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

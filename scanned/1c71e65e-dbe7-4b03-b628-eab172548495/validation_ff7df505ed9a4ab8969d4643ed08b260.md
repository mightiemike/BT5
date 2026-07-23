### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end user. A pool admin who adds the router to the allowlist (the only way to enable router-mediated swaps for legitimate users) inadvertently opens the pool to every user on the public router, defeating the allowlist entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router] ?
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` value is in the per-pool allowlist: [3](#0-2) 

When the user calls through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly — the actual end user's address (`msg.sender` of the router call) is stored only in transient callback storage and is **never forwarded to the pool or the extension**: [4](#0-3) 

The extension therefore sees `sender = router_address` for every user who routes through the router. The allowlist cannot distinguish between different end users — it can only gate the router contract itself.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Result |
|---|---|
| Do **not** add router to allowlist | All router-mediated swaps revert, even for allowlisted users — broken functionality |
| Add router to allowlist | Every user on the public router bypasses the allowlist — full bypass |

Note that `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the LP position owner, passed explicitly by the caller), which correctly identifies the economic beneficiary even when the `MetricOmmPoolLiquidityAdder` is the `msg.sender`. [5](#0-4) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` for regulatory compliance, KYC gating, or restricted-access trading cannot enforce its allowlist against users who route through `MetricOmmSimpleRouter`. Any non-allowlisted user can execute swaps on the pool, consuming LP liquidity and generating fees, in direct violation of the pool's configured access policy. This constitutes a broken core pool functionality with direct fund-impact consequences (unauthorized parties drain LP-owned assets through swaps the pool was designed to block).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint. Pool admins who deploy a `SwapAllowlistExtension` pool and want legitimate allowlisted users to be able to use the router will naturally add the router address to the allowlist — this is the only way to make router-mediated swaps work for those users. The bypass is therefore triggered by a routine, expected admin action, not a sophisticated attack. Any user aware of the router can exploit it immediately after the router is allowlisted.

---

### Recommendation

Pass the **originating user** to the extension rather than the direct pool caller. Two complementary approaches:

1. **Add an `originator` parameter to `IMetricOmmExtensions.beforeSwap`** — the pool passes `msg.sender` as `sender` (the direct caller) and a separate `originator` field that trusted periphery contracts populate (e.g., via a dedicated router-identity callback or a signed payload in `extensionData`). The allowlist extension then gates on `originator`.

2. **Short-term mitigation** — document that `SwapAllowlistExtension` gates the direct pool caller, not the end user, and that adding any public router to the allowlist opens the pool to all users. Pool admins must either (a) require direct pool calls only, or (b) deploy a custom router that enforces the allowlist before calling the pool.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists Alice (legitimate user) AND the router
//    (so Alice can use the router)
// 3. Bob (not allowlisted) calls router.exactInputSingle()

// pool.swap() is called with msg.sender = router
// _beforeSwap(sender=router, ...)
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// Bob's swap succeeds despite not being on the allowlist

function test_allowlistBypassViaRouter() public {
    // Deploy pool with SwapAllowlistExtension
    SwapAllowlistExtension ext = new SwapAllowlistExtension(address(factory));
    address pool = deployPoolWithExtension(address(ext));

    address alice = makeAddr("alice");
    address bob   = makeAddr("bob");   // NOT allowlisted

    // Admin adds Alice AND the router (to enable router swaps for Alice)
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, alice, true);
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, address(router), true); // <-- necessary for Alice to use router

    // Bob is not allowlisted, but routes through the public router
    vm.prank(bob);
    // This succeeds — extension sees sender=router (allowlisted), not bob
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Bob successfully swapped on a pool he was not authorized to access
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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

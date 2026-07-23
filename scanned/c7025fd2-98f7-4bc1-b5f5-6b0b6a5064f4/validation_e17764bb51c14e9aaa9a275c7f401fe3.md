### Title
`SwapAllowlistExtension` checks the router address as the swapper, not the actual end user — any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. Because `MetricOmmPool.swap` passes `msg.sender` (the router contract) as `sender`, the extension always sees the router's address — never the actual end user. If the router is allowlisted (the natural setup for a pool that supports periphery routing), every user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → ExtensionCalling._callExtensionsInOrder(...)
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the actual end user: [3](#0-2) 

The router calls `pool.swap` directly with no forwarding of the original caller's identity: [4](#0-3) 

**Consequence:** The allowlist is keyed on the router address. Two broken outcomes follow:

1. **Bypass (high impact):** If the pool admin allowlists the router (the natural setup for a pool that wants to support periphery routing), every user — including those explicitly excluded — can swap by going through `MetricOmmSimpleRouter`. The per-user allowlist is completely ineffective.
2. **Lockout:** If the pool admin does not allowlist the router, even explicitly allowlisted users cannot swap through the router, breaking core swap functionality for the intended audience.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed by the caller), not on `sender`: [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise approved addresses loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. An unauthorized user can execute swaps against the pool's LP liquidity, receiving output tokens at oracle-anchored prices. This is a direct loss-of-access-control impact on LP funds and pool curation policy, matching the "allowlist bypass → unauthorized trade against LP assets" impact class.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed for the protocol. Any user who discovers the bypass (or simply uses the router as intended) triggers it. No special privileges, flash loans, or unusual token behavior are required. The trigger is a standard `exactInputSingle` call.

---

### Recommendation

Pass the original end-user address through the swap path so the extension can gate on the economically relevant actor. Two approaches:

1. **Preferred — explicit `recipient` or caller field in extension data:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This keeps the core pool generic.
2. **Alternative — add a `payer`/`originator` parameter to `pool.swap`:** The pool accepts an explicit originator address (validated against `msg.sender` or a trusted router list) and passes it as `sender` to extensions. The router fills this with its own `msg.sender`.

Either way, `SwapAllowlistExtension.beforeSwap` must check the address that is economically responsible for the swap, not the intermediate contract that called the pool.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with SwapAllowlistExtension configured.
// Pool admin allowlists the router (standard periphery setup).
// Pool admin does NOT allowlist `attacker`.

// 1. Admin allowlists router
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 2. Attacker (not individually allowlisted) calls router directly
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds: SwapAllowlistExtension saw sender=router (allowlisted),
// never checked attacker's address. Allowlist bypassed.
```

The extension receives `sender = address(router)` and passes the check because the router is allowlisted, while the actual user `attacker` was never verified.

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

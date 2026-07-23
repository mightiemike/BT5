Audit Report

## Title
SwapAllowlistExtension checks router address instead of original trader — allowlist fully bypassed when router is allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap()` — the `MetricOmmSimpleRouter`, not the originating trader. If a pool admin allowlists the router address to permit router-based swaps, every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The allowlist's entire purpose — restricting which end-users may trade — is defeated.

## Finding Description

**Call chain:**

1. Trader calls `MetricOmmSimpleRouter.exactInputSingle()`. The router stores the original `msg.sender` (trader) in transient storage as the *payer* only: [1](#0-0) 

2. The router then calls `pool.swap(params.recipient, ...)`. From the pool's perspective, `msg.sender` is the **router**, not the trader.

3. `MetricOmmPool.swap` passes its own `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

4. `ExtensionCalling._beforeSwap` forwards that `sender` (router) verbatim to the extension: [3](#0-2) 

5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the **router**: [4](#0-3) 

The check `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][trader]`. The original trader's address is stored in transient storage as the payer but is never surfaced to the pool or extension as the actor being gated.

**Why existing guards fail:** `_requireExpectedCallbackCaller` only validates that the swap callback originates from the expected pool — it has no bearing on which address the extension sees as the swapper. There is no mechanism in the router or pool to forward the original `msg.sender` to the extension as the `sender`.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` and allowlists the `MetricOmmSimpleRouter` address (a natural operational choice to enable router-based trading for permitted users) inadvertently grants every unprivileged address the ability to swap. The allowlist is rendered completely ineffective: any trader can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and the extension will approve the swap because `allowedSwapper[pool][router] == true`. This is a broken core pool access-control mechanism causing the allowlist to fail to restrict fund flows as intended.

## Likelihood Explanation
Exploitability requires only that the pool admin has allowlisted the router address — a routine and expected configuration for any pool that intends to support router-based swaps for a subset of users. No special privileges, flash loans, or unusual conditions are needed. Any unprivileged trader can exploit this by simply calling the router. The condition is likely to be met in production deployments.

## Recommendation
The router must pass the originating trader's address to the pool in a way the extension can verify. Options:
- Encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (requires the extension to trust the router, which must be separately verified).
- Add a dedicated `originSender` field to the pool's `swap` interface so the router can supply the true initiator, and the extension checks that field.
- Have the pool expose a transient-storage slot for the original initiator that the router populates before calling `swap`, and the extension reads.

The simplest safe fix is for `SwapAllowlistExtension` to revert if `sender` is a known router and no per-user identity is provided, or for the router to always pass `msg.sender` as part of `extensionData` with a verifiable format.

## Proof of Concept

```solidity
// Setup: pool configured with SwapAllowlistExtension
// Pool admin allowlists the router (to enable router-based swaps)
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attacker: an address NOT individually allowlisted
address attacker = address(0xBEEF);
// allowedSwapper[pool][attacker] == false

// Attacker calls router — extension sees sender=router, which IS allowlisted
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed
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

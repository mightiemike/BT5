### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User — Any User Bypasses Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual EOA. A pool admin who allowlists the router to enable router-mediated swaps for their approved users simultaneously opens the gate to every user on the network, completely defeating the allowlist.

### Finding Description

The call chain for a router-mediated swap is:

```
EOA (charlie) → MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
        → ExtensionCalling._beforeSwap(sender = router, ...)
            → SwapAllowlistExtension.beforeSwap(sender = router, ...)
                → allowedSwapper[pool][router]  ← checks router, not charlie
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` value and forwards it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The pool admin's intended policy is: "only alice and bob may swap." To let alice and bob use the router (a normal, supported periphery path), the admin must call `setAllowedToSwap(pool, router, true)`. The moment they do, the check `allowedSwapper[pool][router]` returns `true` for every caller who routes through the router — including charlie, dave, and any other unprivileged address.

The admin has no way to simultaneously (a) allow approved users to use the router and (b) block unapproved users from using the router, because the extension cannot distinguish the actual initiating EOA from the router intermediary.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on the `owner` argument (the position owner), not the `sender` (the liquidity adder), so the deposit path does not share this flaw: [4](#0-3) 

### Impact Explanation

A curated pool (KYC-gated, institutional, or otherwise restricted) that deploys `SwapAllowlistExtension` and allowlists the router to support the standard periphery path is fully open to any unprivileged user via `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The attacker can execute swaps at oracle prices against the pool's LP liquidity without being on the allowlist. This is a direct admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path through a supported public contract.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and supported by the protocol. Any pool admin who wants their allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. This is the expected operational pattern, making the bypass reachable in every realistic curated-pool deployment that supports router access.

### Recommendation

The extension must recover the actual initiating EOA rather than the immediate `msg.sender` of the pool call. Two approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the EOA) into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention for the identity field in `extensionData`.

2. **Check the `sender` argument against a router registry and fall back to a caller-supplied identity**: The extension recognises known router addresses and reads the real initiator from a transient-storage slot the router writes before calling the pool (analogous to how `MetricOmmSwapRouterBase` already stores the payer in transient storage).

3. **Require direct pool calls for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap` directly. This is a usability restriction but avoids the code change.

Option 1 or 2 is preferred because option 3 breaks the standard periphery UX for curated pools.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup (pseudo-Foundry test):
// 1. Deploy pool with SwapAllowlistExtension wired into BEFORE_SWAP_ORDER.
// 2. Pool admin allowlists alice (approved user) and the router
//    (so alice can use the router):
//      swapExt.setAllowedToSwap(pool, alice, true);
//      swapExt.setAllowedToSwap(pool, router, true);  // ← required for router UX
// 3. Charlie (not allowlisted) calls the router:
//      router.exactInputSingle(ExactInputSingleParams({
//          pool: pool,
//          tokenIn: token0,
//          recipient: charlie,
//          zeroForOne: true,
//          amountIn: 1000,
//          amountOutMinimum: 0,
//          priceLimitX64: 0,
//          extensionData: ""
//      }));
//    → pool.swap(msg.sender=router) → beforeSwap(sender=router)
//    → allowedSwapper[pool][router] == true → PASSES
//    → charlie executes a swap on the curated pool without being allowlisted.
//
// 4. Charlie calls pool.swap() directly:
//      pool.swap(charlie, true, 1000, 0, "", "");
//    → beforeSwap(sender=charlie)
//    → allowedSwapper[pool][charlie] == false → REVERTS NotAllowedToSwap
//
// Result: the allowlist is enforced for direct calls but fully bypassed
// for any user who routes through MetricOmmSimpleRouter.
``` [5](#0-4) [3](#0-2) [2](#0-1)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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

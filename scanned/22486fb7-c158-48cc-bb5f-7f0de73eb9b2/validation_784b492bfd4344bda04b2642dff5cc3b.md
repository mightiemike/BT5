### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted, not the actual user. If the router is allowlisted (the only way to permit router-mediated swaps on a curated pool), every user on the internet can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The router carries no information about which end-user initiated the call.

This creates an irresolvable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all; they must call the pool directly |
| Router **allowlisted** | Every user on the internet can bypass the allowlist by routing through the public router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., a KYC-gated or institution-only pool) with `SwapAllowlistExtension` and allowlists the public `MetricOmmSimpleRouter` to support normal UX inadvertently opens the pool to all users. Any non-allowlisted address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and execute swaps against the pool without restriction. The allowlist guard silently fails open, and the pool's curated invariant is permanently broken for every swap that enters through the router.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary production entry point for swaps. Pool admins who want their allowlisted users to have a normal trading experience will naturally allowlist the router. The router is a public, permissionless contract with no access control of its own. Any user who discovers the bypass can exploit it immediately with a standard router call and no special privileges.

---

### Recommendation

The extension must check the **original user**, not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router.** Add a `payer` / `originator` field to the extension data that the router populates with `msg.sender` before forwarding to the pool. The extension reads and verifies this field instead of (or in addition to) the `sender` argument.

2. **Check `sender` against the allowlist but also validate that `sender` is not a known intermediary.** If `sender` is the router, require that the extension data contains a signed or otherwise authenticated user identity.

The simplest safe fix is option 1: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `sender` is a recognized router address.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
// 2. Pool admin allowlists the router so that allowlisted users can trade normally:
//      swapExtension.setAllowedToSwap(pool, address(router), true);
// 3. Attacker (not individually allowlisted) calls the router:

function test_swapAllowlistBypassViaRouter() public {
    // Pool admin allowlists the router (natural production setup)
    vm.prank(admin);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attacker is NOT individually allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker routes through the public router — extension sees router as sender
    vm.prank(attacker);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            recipient: attacker,
            zeroForOne: true,
            amountIn: 1_000e18,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            deadline: block.timestamp,
            extensionData: ""
        })
    );
    // Swap succeeds — allowlist bypassed
}
```

The extension evaluates `allowedSwapper[pool][router] == true` and passes, regardless of who called the router. [5](#0-4) [6](#0-5) [1](#0-0)

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

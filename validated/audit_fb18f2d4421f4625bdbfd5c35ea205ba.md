Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual end user. If the pool admin allowlists the router to enable legitimate users to use it, every user — including non-allowlisted ones — can bypass the curated pool's access control by routing through the router.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls:

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards this value verbatim as the `sender` argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

**Step 2 — Router is `msg.sender` of `pool.swap()`.**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

So `msg.sender` inside `pool.swap()` is the router contract address, not the end user.

**Step 3 — Extension checks the router address, not the end user.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

Here `msg.sender` is the pool and `sender` is the router. The check `allowedSwapper[pool][router]` passes for every caller of the router — there is no per-user check.

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can conveniently use the router.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The extension sees `sender = router`, finds `allowedSwapper[pool][router] == true`, and allows the swap.
5. The non-allowlisted user successfully swaps in a pool they should be excluded from.

**Existing guards are insufficient:** The only guard is `allowedSwapper[pool][sender]` where `sender` is always the immediate caller of `pool.swap()`. There is no mechanism to propagate or verify the original end-user identity through the router.

## Impact Explanation
The `SwapAllowlistExtension` is the sole access-control mechanism for curated pools. Its bypass allows arbitrary unprivileged users to trade in pools explicitly restricted to a curated set of addresses (e.g., KYC'd, whitelisted counterparties). This breaks the core pool functionality the extension is designed to enforce and constitutes an admin-boundary break: the pool admin's configured access policy is rendered ineffective by a publicly accessible router path.

## Likelihood Explanation
The condition requires the pool admin to allowlist the router — a natural and expected action when the admin wants allowlisted users to be able to use the standard router. Once the router is allowlisted (even for one legitimate user), the bypass is available to every address unconditionally, requires no special privileges, and is repeatable on every swap.

## Recommendation
The extension must verify the actual end user, not the immediate caller. Two viable approaches:

1. **Pass the original user through `extensionData`:** Have the router encode `msg.sender` (the end user) into `extensionData` and have the extension decode and check it. This requires the extension to trust that the pool's `msg.sender` is a known, non-spoofable router.

2. **Maintain a router registry and check the transitive caller:** The extension checks whether `sender` is a registered router; if so, it decodes the real user from `extensionData` and checks that address against the allowlist instead.

3. **Do not allowlist routers; require direct pool interaction:** Document that the allowlist checks the immediate caller and that routing through any intermediary contract will use the intermediary's address.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured.
// 2. allowedSwapper[pool][alice] = true  (alice is the only intended swapper)
// 3. allowedSwapper[pool][router] = true (admin enables router for alice's convenience)

// Attack:
// 4. Bob (non-allowlisted) calls:
//    router.exactInputSingle(ExactInputSingleParams({
//        pool: restrictedPool,
//        recipient: bob,
//        zeroForOne: true,
//        amountIn: 1e18,
//        ...
//    }));
// 5. pool.swap() is called with msg.sender = router.
// 6. beforeSwap(sender=router, ...) checks allowedSwapper[pool][router] == true → passes.
// 7. Bob's swap executes successfully despite not being on the allowlist.
```

A Foundry integration test can confirm this by:
- Deploying the pool with `SwapAllowlistExtension`
- Setting `allowedSwapper[pool][router] = true` and `allowedSwapper[pool][alice] = true`
- Calling `router.exactInputSingle` from an address that is not `alice` and asserting the swap succeeds (no `NotAllowedToSwap` revert).

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

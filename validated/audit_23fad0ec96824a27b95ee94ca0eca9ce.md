Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` always sets to `msg.sender` — the direct caller of the pool. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This creates an irreconcilable configuration failure: either allowlisted users cannot use the router at all, or the admin must allowlist the router itself — which grants any unprivileged user a complete bypass of the allowlist.

## Finding Description

**Exact call chain:**

1. Attacker calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ..., params.extensionData)` at [1](#0-0) 
3. Inside `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, recipient, ...)` at [2](#0-1)  — here `msg.sender` is the **router address**, not the end-user.
4. `ExtensionCalling._beforeSwap` encodes and forwards `sender = router_address` to every configured extension at [3](#0-2) 
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]` at [4](#0-3) 

The extension never sees the original user's address. The pool has no mechanism to propagate end-user identity through the router — `params.recipient` is the output recipient, not the economic actor initiating the swap.

**Existing guards are insufficient:** The only guard in `beforeSwap` is the `allowedSwapper[pool][sender]` mapping check. [5](#0-4)  There is no secondary check on `recipient`, no originator field in the pool interface, and no mechanism in the router to pass the original caller's identity to the extension.

**Two failure modes — neither achieves intended policy:**

| Admin configuration | Result |
|---|---|
| Router NOT allowlisted | Allowlisted users cannot swap through the router → broken core swap functionality |
| Router IS allowlisted | Any user bypasses the allowlist by routing through the router → full bypass |

## Impact Explanation

For any pool deploying `SwapAllowlistExtension` (KYC-gated, institutional, or restricted-access pools), any unprivileged user can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. This is a direct policy bypass enabling unauthorized swaps against a pool explicitly configured to restrict access. Unauthorized swaps extract value from LP positions under conditions the pool admin did not intend to permit — constituting broken core pool functionality and direct loss of user/LP principal. This meets the "Broken core pool functionality causing loss of funds or unusable swap flows" and "Admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation

The router is the primary user-facing entry point for swaps. No special privileges, flash loans, or multi-transaction setup are required. A single `exactInputSingle` call from any EOA suffices. Any user who discovers the pool has a `SwapAllowlistExtension` can trivially exploit this. The exploit is repeatable on every swap.

## Recommendation

The `sender` identity passed to extensions must reflect the economic actor, not the intermediary. The preferred fix is to extend the pool interface with an explicit `originator` field that the router populates with `msg.sender` before calling the pool, and have `SwapAllowlistExtension.beforeSwap` check `originator` instead of (or in addition to) `sender`. Alternatively, the router should be modified to pass the end-user address in `extensionData` in a standardized, authenticated way, and the extension should decode and verify it. Checking `recipient` alone is insufficient as it changes the semantic of the allowlist and `recipient` can be set to any address.

## Proof of Concept

```solidity
// Pool deployed with SwapAllowlistExtension.
// Admin allowlists only `trustedUser`:
// extension.setAllowedToSwap(pool, trustedUser, true);

// Step 1: Attacker (not allowlisted) calls router directly — reverts if router not allowlisted.
// Admin, wanting trustedUser to use the router, allowlists the router:
// extension.setAllowedToSwap(pool, router, true);

// Step 2: Attacker retries via router:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Pool receives msg.sender = router.
// _beforeSwap(router, attacker, ...) is called.
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
// Swap succeeds. Allowlist fully bypassed.
```

### Citations

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

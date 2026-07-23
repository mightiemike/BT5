Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any caller to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the pool admin allowlists the router (the natural action to permit router-mediated swaps), every user who can call the public router bypasses the per-user restriction entirely, rendering the allowlist a no-op for all router paths.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` at line 231 passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap()
  recipient,
  ...
);
``` [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`:**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  ...
}
```

The second parameter (`recipient`) is unnamed and completely ignored. [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with the router as `msg.sender`:**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,   // recipient = end user
    ...
  );
```

The router is `msg.sender` to the pool; the end user's address only appears as `recipient`. [4](#0-3) 

**Root cause:** The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. The `recipient` field — which carries the actual end user's address in router-mediated swaps — is silently discarded at the extension's function signature.

## Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to enforce KYC, whitelist-only, or institutional-access restrictions receives no protection against any user who routes through `MetricOmmSimpleRouter`. Because the router is a public, permissionless contract, the bypass requires no special privilege — any EOA can exploit it. This is an admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path, allowing unauthorized swaps against LP funds in the restricted pool.

## Likelihood Explanation

The router is the primary user-facing swap interface for the protocol. Pool admins who want to restrict swaps while still supporting normal UX will naturally allowlist the router address. The `SwapAllowlistExtension` NatDoc states it "Gates `swap` by swapper address, per pool," implying end-user gating; nothing in the interface or documentation warns that router-mediated swaps substitute the router's address for the user's. The mismatch is invisible until the bypass is exercised and is trivially repeatable by any EOA.

## Recommendation

Use the `recipient` parameter as the identity to gate, or require the router to forward the originating user's address inside `extensionData`. The minimal correct fix:

```solidity
function beforeSwap(
    address sender,
    address recipient,
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata
) external view override returns (bytes4) {
    address swapper = recipient != address(0) ? recipient : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, document explicitly that the extension gates the **direct caller of `pool.swap()`** and that router-mediated swaps are gated by the router address, so pool admins can make an informed decision.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps (natural setup).
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(recipient=bob, ...)` — `msg.sender` to pool is the router.
6. Pool calls `extension.beforeSwap(sender=router, recipient=bob, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was never allowlisted for, bypassing the access control entirely.

A Foundry integration test can confirm this by: deploying the pool with the extension, setting only Alice and the router as allowed, then asserting that a `router.exactInputSingle` call from Bob's address succeeds rather than reverting with `NotAllowedToSwap`.

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

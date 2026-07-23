Audit Report

## Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the real end-user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's immediate `msg.sender` — the router contract — not the originating user. When any user routes through `MetricOmmSimpleRouter`, the extension evaluates the router's address against the allowlist, not the user's. This creates an impossible configuration: either the router is allowlisted (bypassing per-user gating for everyone) or it is not (breaking router-mediated swaps for all users, including legitimately allowlisted ones).

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap`:**

The extension enforces its guard as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool.

**How the pool populates `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [2](#0-1) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the `sender` argument forwarded to every extension in the `BEFORE_SWAP_ORDER`: [3](#0-2) 

So `sender` received by the extension is always the pool's `msg.sender` — the immediate caller of `pool.swap()`.

**How the router calls the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

The pool's `msg.sender` is the router contract. The pool therefore calls `_beforeSwap(router_address, recipient, ...)`, and the extension evaluates `allowedSwapper[pool][router_address]` — not the actual end-user.

The same misbinding applies to every hop in `exactInput`: [5](#0-4) 

`_setNextCallbackContext` only sets the *payer* for the token-pull callback; it does not affect what `sender` the pool forwards to the extension. For every hop the extension still sees the router address.

**The impossible configuration:**

The per-pool allowlist mappings are: [6](#0-5) 

A pool admin who intends to allowlist individual users faces an impossible choice:
- **Do not allowlist the router** → all router-mediated swaps revert for everyone, including legitimately allowlisted users. Core swap functionality is broken for the primary user-facing path.
- **Allowlist the router** → every unprivileged user can bypass the per-user gate by routing through the public router, defeating the entire allowlist.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Impact Explanation

**High.** A pool deployed with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., institutional counterparties, KYC'd users, or whitelisted market makers) can be freely traded against by any address through `MetricOmmSimpleRouter`. The allowlist guard — the sole access-control mechanism on the swap path — is silently bypassed. This allows unauthorized parties to execute swaps at oracle-anchored prices the pool admin intended to restrict, extract value from LP positions, or drain protocol fees from a pool that was meant to be private. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break by an unprivileged path.

## Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary, publicly documented user-facing entry point for swaps. No special privilege, token, or setup is required. Any EOA or contract can call `exactInputSingle` or `exactInput` on any pool. The bypass requires zero front-running, zero flash loans, and zero admin interaction — a single public transaction suffices.

## Recommendation

The pool must forward the original end-user identity to the extension, not the immediate caller. Two complementary fixes:

1. **Pool-level (definitive fix):** Add an explicit `originator` parameter to `pool.swap()` that the router populates with `msg.sender` (the real user), and have the pool forward that value — not its own `msg.sender` — as `sender` to `_beforeSwap`.

2. **Extension-level (short-term):** Until the pool interface is updated, `SwapAllowlistExtension` should check the `recipient` field (which the router sets to the real user's address for single-hop swaps) or require pools using this extension to be called directly, not through the router. Alternatively, the extension could maintain a registry of trusted routers and, when `sender` is a trusted router, check `allowedSwapper[pool][recipient]` instead.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in the `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. The router calls `pool.swap(bob, ...)`. Pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, recipient=bob, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]`.
7. If admin previously allowlisted the router (to allow Alice to use it), Bob's call passes — allowlist bypassed.
8. If admin never allowlisted the router, Alice also cannot use the router — core functionality broken.
9. Bob's swap executes at oracle-anchored prices on a pool the admin intended to restrict to Alice only.

A Foundry integration test can confirm this by: deploying the pool with the extension, allowlisting only Alice, allowlisting the router (to simulate the "working" configuration for Alice), then calling `exactInputSingle` as Bob and asserting the swap succeeds despite Bob not being in `allowedSwapper`.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

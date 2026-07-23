Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. Any pool admin who allowlists the router to support legitimate router-mediated swaps simultaneously grants unrestricted swap access to every user of the public router, fully nullifying the allowlist.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim into the encoded call to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the router when the call originates from `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(recipient, ...)` directly, with no encoding of the original `msg.sender` into `extensionData`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. The router is a public, permissionless contract — no role or privilege is required to call it. The pool admin faces an impossible choice: do not allowlist the router (blocking all legitimate router users) or allowlist the router (granting every network participant unrestricted swap access). The second branch is the exploitable path.

## Impact Explanation

A pool using `SwapAllowlistExtension` is intended to restrict swaps to a curated counterparty set (e.g., KYC'd addresses, whitelisted market makers). Once the router is allowlisted to support legitimate users, the restriction is fully nullified. Any unprivileged user can call the public router targeting the restricted pool, pass the allowlist check unconditionally (because `sender = router` is allowlisted), and execute swaps at oracle-derived prices against LP liquidity deposited under the assumption of a restricted counterparty set. LPs suffer unexpected adverse selection and impermanent loss; the pool's core access-control invariant is broken. This constitutes a direct loss of LP principal above Sherlock contest thresholds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, deployed periphery contract requiring no special role. Any pool that enables `SwapAllowlistExtension` and also wants to support router-mediated swaps for legitimate users must allowlist the router, triggering the vulnerability automatically. The attacker requires no special setup — a single call to any of the router's swap entry points suffices. The condition is self-inflicted by the pool admin following the only available path to support router users.

## Recommendation

The `beforeSwap` hook must gate the originating user, not the immediate `msg.sender` of the pool. The cleanest fix is Option 1 from the submission: have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when the call originates from a recognized trusted router, falling back to `sender` for direct pool calls. Concretely:

- In the router's swap entry points, append `abi.encode(msg.sender)` to `extensionData` before calling `pool.swap`.
- In `SwapAllowlistExtension.beforeSwap`, if `sender` is a registered trusted router and `extensionData` is non-empty, decode the true user from `extensionData` and check `allowedSwapper[pool][trueUser]` instead of `allowedSwapper[pool][sender]`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required to allow legitimate users to use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle(
       pool, zeroForOne=true, amountIn, recipient=attacker, ...
     )
  2. Router calls pool.swap(recipient=attacker, zeroForOne, amount, priceLimit, "", extensionData)
     with msg.sender = router.
  3. MetricOmmPool._beforeSwap(sender=router, ...) →
     SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. allowedSwapper[pool][router] == true → no revert.
  5. Swap executes. Attacker receives token1 at oracle price.

Expected: revert NotAllowedToSwap() — attacker is not individually allowlisted.
Actual:   swap succeeds — the router, not the attacker, is the checked identity.
```

A Foundry integration test can confirm this by deploying the pool with `SwapAllowlistExtension`, allowlisting only the router address, then calling `exactInputSingle` from an unallowlisted EOA and asserting the swap succeeds (demonstrating the bypass) rather than reverting with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

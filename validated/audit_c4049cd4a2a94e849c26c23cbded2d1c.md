Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the pool admin allowlists the router — the only way to enable router-mediated swaps — every unprivileged user can bypass the allowlist by calling the router instead of the pool directly, completely defeating the access-control invariant of the extension.

## Finding Description

**Root cause:** `MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the **router**, not the end user. The check resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to encode the real caller into `extensionData` — it passes `""` as `extensionData`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← caller-supplied; no enforced encoding of msg.sender
  );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps.
2. Admin calls `setAllowedToSwap(pool, router, true)` — the only way to enable router usage.
3. Admin calls `setAllowedToSwap(pool, alice, true)` — alice is the intended allowlisted user.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(bob_recipient, ...)` with `msg.sender = router`.
6. Extension checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes despite not being allowlisted.

**Direct call comparison (guard works correctly):**
- Bob calls `pool.swap(...)` directly → extension checks `allowedSwapper[pool][bob] == false` → reverts.

The bypass is the structural collapse of all end-user identities into the single router address.

## Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting which addresses may trade in a pool. Once the router is allowlisted (required for any router-mediated swap), the allowlist is completely ineffective: any address can call the router and execute swaps the pool admin intended to block. This breaks the core access-control invariant of the extension. Unauthorized parties can trade at oracle-derived prices, bypassing any KYC, whitelist, or rate-limiting intent encoded in the allowlist — constituting a broken core pool functionality with direct fund-impact potential (unauthorized parties drain pool liquidity at oracle prices).

## Likelihood Explanation

The scenario is triggered whenever a pool admin: (1) deploys a pool with `SwapAllowlistExtension` to restrict swaps, and (2) calls `setAllowedToSwap(pool, router, true)` to enable router usage. Step 2 is not a mistake in isolation — it is the only operational path to enable router-mediated swaps — but it silently opens the gate to all users. No special privileges, flash loans, or oracle manipulation are required. Any unprivileged user who discovers the router is allowlisted can immediately exploit this.

## Recommendation

`SwapAllowlistExtension.beforeSwap` must gate the **end user**, not the intermediary. The cleanest fix is for the router to encode `msg.sender` into `extensionData`, and for the extension to decode and verify it when present, falling back to `sender` for direct pool calls. Concretely:

1. **Router change:** In each swap entry point, encode `msg.sender` into `params.extensionData` before passing it to `pool.swap()`.
2. **Extension change:** In `beforeSwap`, decode the real caller from `extensionData` when it is present and non-empty; use that address for the allowlist check instead of `sender`.

An alternative is to check `recipient` in addition to `sender`, which closes the bypass for the common case where the user is also the recipient — but this is incomplete since `recipient` can be any address.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // enables router usage
  admin calls setAllowedToSwap(pool, alice, true)    // alice is intended allowlisted user
  bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({
      pool: pool,
      tokenIn: token1,
      tokenOut: token0,
      zeroForOne: false,
      amountIn: X,
      recipient: bob,
      extensionData: ""
  })

  pool.swap() called with msg.sender = router
  _beforeSwap(sender=router, recipient=bob, ...)
  SwapAllowlistExtension: allowedSwapper[pool][router] == true  ✓
  Swap executes — bob receives token0 despite not being allowlisted

Direct call (guard works):
  bob calls pool.swap(...) directly
  _beforeSwap(sender=bob, ...)
  SwapAllowlistExtension: allowedSwapper[pool][bob] == false  ✗
  Reverts with NotAllowedToSwap
```

Foundry test outline:
- Deploy pool with `SwapAllowlistExtension`.
- `setAllowedToSwap(pool, router, true)`.
- Assert `router.exactInputSingle(...)` called by an un-allowlisted `bob` succeeds.
- Assert `pool.swap(...)` called directly by `bob` reverts with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

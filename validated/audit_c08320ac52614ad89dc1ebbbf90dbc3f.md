Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the real swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to restrict swaps on a curated pool to approved addresses. However, it checks the `sender` argument forwarded by the pool, which is the pool's own `msg.sender`. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router (the natural fix for allowing approved users to use the canonical entry point) simultaneously grants every unprivileged address the ability to bypass the allowlist entirely.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231. [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that `sender` value and dispatches it to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`. [2](#0-1) 

**Step 2 — Router substitutes itself as `msg.sender` with no identity forwarding.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The `extensionData` field is passed through from the caller but is never automatically populated with the original caller's address; the router provides no mechanism to embed the real user identity. [3](#0-2) 

**Step 3 — Extension evaluates the wrong actor.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router. The real user's address is never consulted. [4](#0-3) 

**Existing guards are insufficient.** The `allowAllSwappers` flag is a pool-level escape hatch that disables the allowlist entirely — it does not help distinguish router-mediated calls from direct calls. There is no on-chain mechanism in the current extension or router to recover the original EOA identity. [5](#0-4) 

## Impact Explanation

LP providers who deposit into a curated pool with `SwapAllowlistExtension` do so under the assumption that only vetted counterparties will trade against their liquidity. When the admin allowlists the router (the only way to let approved users use the canonical entry point), every unprivileged address gains the ability to execute swaps against the pool. This directly exposes LP principal to adversarial order flow — unauthorized traders can extract value from the pool at oracle-derived prices — satisfying the "direct loss of user principal" and "broken core pool functionality" impact gates. Severity: **Medium** (requires the admin to take the natural remediation step of allowlisting the router).

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, production-grade entry point. Any pool admin who wants allowlisted users to be able to use the router will naturally allowlist the router address (Path B). The bypass requires no special privileges, no flash loans, and no exotic tokens — only a standard `exactInputSingle` or `exactInput` call. The precondition (router allowlisted) is the expected operational state for any pool that intends to support router-based swaps for its approved users.

## Recommendation

The extension must recover the original user identity rather than relying on the `sender` argument forwarded by the pool. Two complementary fixes:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated change in the router and extension and is the cleanest on-chain fix, preserving the extension as the single source of truth.

2. **Check `sender` at the router level before calling the pool**: The router reads the allowlist and reverts before the pool call if the caller is not approved. This adds a router-level gate but requires the router to know about each pool's allowlist configuration.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (Alice is the intended gated user)
  - allowedSwapper[pool][router] = true  (admin adds this so Alice can use the router)

Attack:
  1. Charlie (not allowlisted) calls:
       router.exactInputSingle({pool: curated_pool, recipient: charlie, ...})
  2. Router calls pool.swap(recipient=charlie, ...)
       → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates:
       allowedSwapper[pool][router] == true  → passes
  5. Swap executes. Charlie receives tokens from the curated pool.
     The allowlist guard was a no-op for Charlie's actual address.
```

Foundry test outline:
- Deploy `SwapAllowlistExtension`, configure pool with it
- `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`
- Call `router.exactInputSingle` from Charlie's address
- Assert the swap succeeds (allowlist bypassed) and Charlie receives output tokens

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

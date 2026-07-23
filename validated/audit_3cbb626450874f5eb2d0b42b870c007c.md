### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any unprivileged user to bypass a curated pool's allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `beforeSwap` hook. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. `SwapAllowlistExtension.beforeSwap` keys its allowlist lookup on `(pool, sender)`. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed on a curated pool), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` invokes `_beforeSwap` with `msg.sender` as the `sender` parameter: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

The `SwapAllowlistExtension.beforeSwap` hook performs its allowlist lookup keyed on `(pool, sender)`. When the call originates from `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address.

The pool admin faces an inescapable dilemma:

- **Do not allowlist the router** → allowlisted users cannot use the router at all; they must call `pool.swap` directly.
- **Allowlist the router** → the router address passes the `allowedSwapper` check for every caller, so any unprivileged user can swap on the curated pool by routing through the router.

There is no configuration that simultaneously permits router-mediated swaps for approved users and blocks router-mediated swaps for unapproved users, because the hook has no access to the original end-user identity.

The factory's `createPool` loop initialises extensions after deployment: [3](#0-2) 

Nothing in the initialization path or the extension dispatch corrects the identity mismatch; the `sender` field is structurally bound to the immediate pool caller throughout the entire hook pipeline.

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-guarded pool to enforce a curated or regulated trading environment (e.g., KYC/AML, institutional-only) and who allowlists the router to support normal UX loses the entire protection. Any unprivileged address can call `MetricOmmSimpleRouter.exactInput` or `exactOutput`, the router becomes the pool's `msg.sender`, the allowlist check passes, and the swap executes. The attacker can drain pool liquidity at oracle-anchored prices, trade against the pool's LP positions without authorization, and extract value that the pool admin intended to restrict to approved counterparties. This is a direct loss of LP assets and a broken core pool functionality.

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a step that is operationally necessary for any curated pool that wants to support the standard periphery UX. The attacker needs no special privilege, no custom token, and no multi-step setup: a single `exactInput` call through the router suffices. Any curated production pool that enables router access is immediately exploitable by any address.

### Recommendation

The `SwapAllowlistExtension` must gate the actual end-user identity, not the immediate pool caller. Two sound approaches:

1. **Forward the original caller through `extensionData`**: require the router to ABI-encode the original `msg.sender` into `extensionData` and have the extension verify and decode it. The extension must reject any call where `extensionData` is absent or where `sender == router` without a valid inner-caller proof.
2. **Check `sender` only when `sender` is not a trusted router; otherwise check the decoded inner caller**: maintain a registry of trusted routers in the extension and require them to supply the real caller.

Either approach must be enforced at the extension level so that a direct pool call (where `sender` is the real user) and a router-mediated call (where `sender` is the router but the real user is in `extensionData`) are both correctly gated.

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin allowlists Alice (a KYC'd user):
       allowedSwapper[pool][alice] = true
3. Pool admin allowlists the router (required for Alice to use the router):
       allowedSwapper[pool][router] = true

Attack (Bob, not allowlisted)
──────────────────────────────
4. Bob calls MetricOmmSimpleRouter.exactInput({
       path: [token0, pool, token1],
       recipient: bob,
       amountIn: X,
       amountOutMinimum: 0,
       extensionData: ""
   })
5. Router calls pool.swap(bob, zeroForOne, X, priceLimit, callbackData, "")
   → pool records msg.sender = router
6. pool._beforeSwap(sender=router, ...)
   → SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true → PASSES
7. Swap executes; Bob receives token1 from the curated pool.

Result: Bob, who is not on the allowlist, successfully swaps on the curated pool.
The allowlist protection is completely bypassed.
```

The root cause is structurally identical to the external `buyLoan` report: a critical identity check (`msg.sender` / pool-token compatibility) is performed on the wrong object (the router / the fake pool) rather than on the economically relevant actor (the end user / the loan's actual token pair), allowing an unprivileged caller to satisfy the guard using a mismatched intermediary.

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L206-210)
```text
    for (uint256 i = 0; i < params.extensions.length; i++) {
      CallExtension.callExtension(
        params.extensions[i], abi.encodeCall(IMetricOmmExtensions.initialize, (pool, params.extensionInitData[i]))
      );
    }
```

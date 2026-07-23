Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router intermediary instead of the end user, allowing any unprivileged router caller to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of `pool.swap()`. When a user trades through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract address, not the end user. A pool admin who allowlists the router to enable standard UX for their curated users inadvertently grants swap access to every router caller, completely defeating the per-user allowlist invariant.

## Finding Description
**Root cause — wrong identity checked:**

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged into the ABI-encoded extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [3](#0-2) 

**Router call path — router is `sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The router is `msg.sender` of that call, so `sender` arriving at the extension is the router address, not the end user: [4](#0-3) 

**No valid configuration exists:** If the admin allowlists only individual users, those users cannot swap through the router (router not allowlisted → reverts). If the admin allowlists the router to enable standard UX, every address that calls `router.exactInputSingle(...)` passes the check — the allowlist is fully bypassed. There is no configuration that achieves "only allowlisted users can swap through the router."

**Existing guards are insufficient:** The only guard in `beforeSwap` is `allowedSwapper[pool][sender]`. There is no secondary check on `recipient`, `extensionData`, or any other field that could identify the true economic actor.

## Impact Explanation
A pool deployed with `SwapAllowlistExtension` and the router allowlisted has its curation boundary completely broken for all router-routed swaps. Any unprivileged address can call `router.exactInputSingle(...)` and execute swaps in a pool intended to be restricted. This constitutes broken core pool functionality: the configured security boundary does not hold on the supported public entrypoint (`MetricOmmSimpleRouter`). LPs in curated pools (e.g., pools designed to exclude informed traders) are exposed to adverse selection from all router users, causing direct LP value extraction. This meets the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation
Medium. The pool admin must take the natural step of allowlisting the router to enable standard UX for their curated users. The `setAllowedToSwap(pool, router, true)` call is syntactically identical to allowlisting any individual user — there is no API-level signal that it grants access to all router callers. The bypass is repeatable by any unprivileged address with no special capability required beyond calling the public router.

## Recommendation
The extension must gate on the economic actor (end user), not the intermediary contract. The cleanest fix is to require the router to encode `msg.sender` (the originating user) into `extensionData`, and have the extension decode and verify it when `sender` is a known router address. Concretely:

1. In `MetricOmmSimpleRouter.exactInputSingle` (and other swap entry points), ABI-encode `msg.sender` into `params.extensionData` before passing it to `pool.swap`.
2. In `SwapAllowlistExtension.beforeSwap`, when `sender` is a recognized router address, decode the originating user from `extensionData` and check `allowedSwapper[pool][originatingUser]` instead of `allowedSwapper[pool][sender]`.

Alternatively, document that `SwapAllowlistExtension` is incompatible with router-based swaps and enforce direct pool calls for curated pools — but this breaks standard UX.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls: setAllowedToSwap(pool, alice, true)
    (alice is the intended curated user)
  - Pool admin calls: setAllowedToSwap(pool, router, true)
    (to allow alice to use the standard router UX)

Attack:
  - charlie (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: charlie, ...})
  - Router calls: pool.swap(charlie, ...) [msg.sender = router]
  - Pool calls: _beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] → true → PASSES
  - charlie successfully swaps in the restricted pool

Verification:
  - charlie receives output tokens
  - Pool state is mutated as if charlie were an allowlisted user
  - The per-user allowlist is completely bypassed

Foundry test sketch:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, alice, true) and setAllowedToSwap(pool, router, true).
  3. Fund charlie with tokenIn; charlie calls router.exactInputSingle(...).
  4. Assert swap succeeds and charlie receives tokenOut despite not being in allowedSwapper.
```

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

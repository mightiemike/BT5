### Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual end-user, enabling allowlist bypass or breaking router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. The pool always passes `msg.sender` of its own `swap` call as `sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. The extension therefore checks the router's allowlist entry instead of the real trader's, producing a wrong-actor binding with two fund-impacting outcomes: (1) allowlisted users cannot use the router at all, breaking the primary swap path; (2) if the pool admin allowlists the router to restore router access, every non-allowlisted user can bypass the curated pool's swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry-point) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the originating EOA: [4](#0-3) 

The pool's `swap` interface carries no explicit `sender` parameter that the router could use to forward the real user's address; the only identity the pool can observe is `msg.sender`. There is no mechanism in `extensionData` or elsewhere that `SwapAllowlistExtension` uses to recover the original caller.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the economic actor), not by `sender` (the immediate caller), so the deposit path does not share this flaw: [5](#0-4) 

---

### Impact Explanation

**Scenario A — Broken core swap flow (no admin action required):**
A pool is deployed with `SwapAllowlistExtension` and a curated set of allowlisted EOAs. An allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle`. The pool sees `sender = router`; the router is not in `allowedSwapper`; the hook reverts with `NotAllowedToSwap`. The allowlisted user cannot use the protocol's primary swap periphery at all. This is broken core pool functionality for every allowlisted pool.

**Scenario B — Allowlist bypass (one reasonable admin action):**
The pool admin, observing that allowlisted users cannot use the router, adds the router to `allowedSwapper` for the pool. Now `allowedSwapper[pool][router] = true`. Any non-allowlisted EOA calls `router.exactInputSingle` targeting the curated pool. The pool sees `sender = router`; the hook passes; the swap executes. The entire swap allowlist is bypassed for every user who routes through the router, defeating the curation policy and allowing unauthorized trading on a pool that was designed to restrict access (e.g., KYC-gated, institutional, or risk-limited pools).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented primary periphery for swaps. Any pool that configures `SwapAllowlistExtension` and expects users to interact through the router will immediately hit Scenario A. Scenario B follows as the natural admin remediation. Both paths are reachable by any public user with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The `SwapAllowlistExtension` should gate by the actual end-user, not the immediate pool caller. Two approaches:

1. **Preferred — gate by `recipient` or pass the real user via `extensionData`**: Modify the router to encode the originating `msg.sender` inside `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a signature or trusted-router check).

2. **Alternative — align with the deposit pattern**: If the pool's swap interface is extended to carry an explicit `originator` field (analogous to `owner` in `addLiquidity`), the extension can check that field instead of `sender`.

Until fixed, pools that require a swap allowlist must not rely on `SwapAllowlistExtension` when the router is a supported entry-point.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is KYC-approved)
  allowedSwapper[pool][bob]   = false  (bob is not approved)

Scenario A — broken functionality:
  alice calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] → false
    → revert NotAllowedToSwap
  alice (allowlisted) cannot use the router. ✗

Scenario B — bypass after admin remediation:
  admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (to fix alice's problem above)

  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] → true
    → swap executes for bob (non-allowlisted user)
  bob bypasses the curated pool's swap gate. ✗
```

The root cause is that `sender` in `beforeSwap` is always the immediate pool caller (`msg.sender` of `pool.swap`), which is the router, not the originating EOA. The allowlist invariant — "only approved addresses may trade on this pool" — is broken for every router-mediated swap once the router is allowlisted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

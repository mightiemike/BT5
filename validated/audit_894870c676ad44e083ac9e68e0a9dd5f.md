The full call chain is now clear. Here is the assessment:

---

### Title
Router-Mediated Swaps Check Router Address Instead of End User in SwapAllowlistExtension — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the **router contract**, not the end user. The hook therefore checks whether the router is allowlisted, not whether the actual user is allowlisted. This breaks the allowlist invariant in both directions.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender` = User
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — pool sees `msg.sender` = **Router**
3. Pool's `swap()` calls `_beforeSwap(msg.sender, ...)` passing **Router** as `sender` [1](#0-0) 

4. `ExtensionCalling._beforeSwap` encodes `sender` (= Router) and dispatches to the extension [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` receives `sender` = **Router**, `msg.sender` = **Pool**, and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The router passes `msg.sender` (the user) only to the callback context for payment, not to the pool's `swap()` call as the identity parameter: [4](#0-3) 

There is no mechanism in the current design to forward the original end-user identity through the router to the hook in a trustless way.

**Two concrete failure modes:**

**A — Allowlist bypass (attacker path):** If the pool admin allowlists the router address (e.g., to allow "general" router access while blocking direct pool calls), then *any* unprivileged user can bypass the per-user allowlist simply by routing through `MetricOmmSimpleRouter`. The hook sees `allowedSwapper[pool][router] = true` and passes for every user.

**B — Allowlist denial (legitimate user blocked):** If the pool admin allowlists specific EOAs but not the router, those allowlisted users cannot use the router at all — the hook sees `allowedSwapper[pool][router] = false` and reverts, even though the user is individually authorized.

### Impact Explanation

Scenario A is a direct policy bypass on curated/permissioned pools. The pool admin intended to gate specific addresses; any user can circumvent this by routing through the public router. This breaks the core access-control invariant of the extension and constitutes broken core pool functionality for allowlisted pools.

### Likelihood Explanation

Allowlisting the router is a natural and expected admin action — pool admins who want to allow "all router users" while blocking raw pool calls would do exactly this. The bypass is then trivially exploitable by any user with no special privileges, no front-running, and no two-transaction setup required. The two-transaction framing in the question is a red herring; the bypass is single-transaction.

### Recommendation

The `beforeSwap` hook must be keyed to the economic actor, not the transport layer. Options:
- Pass the original `msg.sender` (end user) through `extensionData` from the router, and have the extension verify it against a router-signed attestation — but this is trust-dependent.
- More robustly: the pool/extension design should distinguish between `sender` (transport) and `originator` (economic actor), and the allowlist should gate on `originator`. The router would need to be a trusted forwarder that attests the originator identity in a verifiable way.
- Alternatively, document that `SwapAllowlistExtension` only supports direct pool calls and revert if `sender` is a known router.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true  (admin allowlists the router)
  allowedSwapper[pool][attacker] = false (attacker is NOT allowlisted)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)  [msg.sender = router]
  → pool calls _beforeSwap(router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap proceeds for attacker despite attacker not being allowlisted

Result: allowlist bypassed; attacker swaps on a curated pool they were not authorized to access.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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

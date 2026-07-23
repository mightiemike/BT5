### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. The extension therefore checks whether the router is allowlisted, not whether the actual economic actor is allowlisted. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the pool to every user on the router.

---

### Finding Description

**Call chain for a direct swap (correct):**
```
User → pool.swap(...)
  pool: _beforeSwap(msg.sender=User, ...)
  SwapAllowlistExtension.beforeSwap(sender=User, ...)
    checks allowedSwapper[pool][User]  ✓
```

**Call chain for a router-mediated swap (broken):**
```
User → MetricOmmSimpleRouter.exactInputSingle(...)
  router: pool.swap(recipient, ...)          // msg.sender at pool = router
  pool: _beforeSwap(msg.sender=Router, ...)
  SwapAllowlistExtension.beforeSwap(sender=Router, ...)
    checks allowedSwapper[pool][Router]      ← wrong actor
```

In `MetricOmmPool.swap`, the `sender` forwarded to the extension is always `msg.sender`:

```solidity
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap, i.e. the router
  recipient,
  ...
);
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls the pool directly without forwarding the original user identity:

```solidity
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

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

---

### Impact Explanation

**Scenario A – Router is allowlisted (bypass):** A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper`. Once the router is allowlisted, every user who calls `exactInputSingle`, `exactInput`, or `exactOutput` through the router passes the check regardless of their own allowlist status. The per-user curation is completely defeated.

**Scenario B – Router is not allowlisted (broken functionality):** Allowlisted users cannot use the router at all; their router-mediated swaps revert with `NotAllowedToSwap`. The pool is only usable via direct `pool.swap` calls, which require the caller to implement `IMetricOmmSwapCallback`. This breaks the core periphery flow for curated pools.

In Scenario A the impact is a direct policy bypass: non-allowlisted users execute swaps against a pool that was explicitly configured to exclude them, draining LP value or violating regulatory/compliance constraints the pool admin intended to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the periphery. Any pool admin who deploys a curated pool and then allowlists the router (a natural step to make the pool usable for their allowlisted users) immediately opens the bypass to all users. The attacker needs no special privileges: they call a public router function with a valid pool address and any `extensionData`.

---

### Recommendation

The extension must check the **economic actor**, not the intermediate contract. Two concrete options:

1. **Pass the original initiator through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Check `sender` only when `sender` is not a known router; otherwise decode the real user from `extensionData`:** The extension maintains a registry of trusted routers and, when `sender` is a trusted router, reads the real user from the extension payload.

3. **Require direct pool calls for allowlisted pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject extension configurations that pair `SwapAllowlistExtension` with a non-zero router allowlist entry).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   (admin allowlists router to support curated users)
  - allowedSwapper[pool][alice] = true    (alice is a curated user)
  - allowedSwapper[pool][bob] = false     (bob is NOT curated)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → passes
    → bob's swap executes against the curated pool
```

Bob, a non-allowlisted user, successfully swaps against a pool that was configured to exclude him, because the extension checks the router's allowlist entry rather than Bob's.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

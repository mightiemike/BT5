### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is the immediate `msg.sender` of `pool.swap()` — against a per-pool allowlist. When users route through `MetricOmmSimpleRouter`, the router becomes `sender`. If the router is allowlisted (required for any router-mediated swap to work), every unprivileged user bypasses the per-user gate entirely by calling the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller), and `sender` is the value the pool received as its own `msg.sender` when `swap()` was called.

In `MetricOmmPool`, `_beforeSwap` is invoked with `msg.sender` forwarded as `sender`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = pool's msg.sender
    )
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is therefore the **router address**, not the end user. The extension receives `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]`.

This creates an irreconcilable conflict:

| Scenario | Result |
|---|---|
| Pool admin allowlists individual users (not the router) | Legitimate users are blocked when going through the router; they must call the pool directly |
| Pool admin allowlists the router (to enable router-mediated swaps) | **Every user bypasses the per-user allowlist** by calling the public, permissionless router |

Neither configuration achieves the intended goal of "only specific users may swap."

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd addresses, institutional counterparties, or any specific set of users loses that restriction entirely the moment the router is allowlisted. `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` and have the pool see `sender = router`, bypassing the per-user gate. This breaks the core access-control invariant of the extension and allows unauthorized users to execute swaps against a pool that was explicitly configured to exclude them.

**Severity: Medium** — direct bypass of a configured security guard; no admin privilege or special setup required beyond the router being allowlisted (which is the only way to support normal router usage).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for the protocol. Any pool operator who deploys `SwapAllowlistExtension` and also wants users to be able to use the standard router faces this conflict. The bypass requires only a standard call to a public function on a deployed contract — no flash loans, no special tokens, no privileged access.

---

### Recommendation

The extension must receive the **original end-user address**, not the intermediate router address. Two viable approaches:

1. **Router-forwarded identity**: The router encodes the actual `msg.sender` into `extensionData` and the extension decodes it. The extension must then verify the forwarding contract is trusted (e.g., a factory-registered router), otherwise the field is spoofable.

2. **Dedicated sender field in the pool interface**: The pool passes both `msg.sender` (the immediate caller) and an explicit `originator` field that the router populates with the actual user. The extension gates on `originator` when the caller is a trusted router.

The current design where `sender` is always the immediate pool caller cannot support per-user allowlisting through any intermediary contract.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for any router-mediated swap.
3. Unprivileged user `0xAttacker` (not individually allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: targetPool,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
   }));
   ```
4. Router calls `pool.swap(...)` — pool's `msg.sender = router`.
5. `_beforeSwap` passes `sender = router` to the extension.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `0xAttacker` successfully swaps against a pool that was configured to exclude them.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. A pool admin who allowlists the router (the only way to let EOA users swap) inadvertently allowlists every user reachable through the router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this `sender` and calls each extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

At this point `msg.sender` of `pool.swap()` is the **router address**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an inescapable dilemma for the pool admin:

| Admin action | Consequence |
|---|---|
| Allowlist the router | Every user reachable through the router bypasses the allowlist |
| Do not allowlist the router | Allowlisted EOA users cannot swap at all (EOAs cannot implement `metricOmmSwapCallback`) |

The same wrong-actor binding applies to all router entry points: `exactInput`, `exactOutputSingle`, and `exactOutput`, including intermediate hops in `_exactOutputIterateCallback` where the router still calls `pool.swap()` as `msg.sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is fully bypassed. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool. The extension sees the router address as `sender`, and if the router is allowlisted (the only way to enable normal EOA swap flow), the check passes unconditionally. The attacker receives pool output tokens and the pool receives input tokens at oracle-derived prices — a direct, fund-impacting trade that the pool admin explicitly intended to block.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap contract. EOAs cannot call `pool.swap()` directly because the pool immediately calls back `msg.sender.metricOmmSwapCallback()`, which reverts for non-contract callers. Therefore, any pool admin who wants allowlisted users to actually trade must allowlist the router, which simultaneously opens the pool to all users. The bypass requires no special knowledge, no privileged access, and no front-running — any user can call the router.

---

### Recommendation

The pool must receive the original user's address through a trusted channel. Two approaches:

1. **Router forwards original caller via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension verifies the encoded address against the allowlist and validates that the call came from the trusted router. This requires a coordinated extension + router design.

2. **Pool-level sender tracking**: The pool stores the original initiator (e.g., via a transient slot set by the router before calling `pool.swap()`) and passes it as a separate field to extensions, distinct from the direct `msg.sender`.

The simplest safe fix is to have the router encode `abi.encode(msg.sender)` into `extensionData` and have `SwapAllowlistExtension` decode and check that address when `msg.sender` (the pool's caller) is a known trusted router.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Admin calls setAllowedToSwap(pool, router, true)   // must allowlist router for EOA swaps
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)
  - LP adds liquidity to the pool

Attack:
  1. attacker calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender of pool.swap() = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  5. Swap executes; attacker receives output tokens

Result:
  attacker successfully swaps on a pool that was supposed to block them.
  The allowlist is completely ineffective for any user who routes through the router.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

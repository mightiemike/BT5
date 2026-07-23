### Title
`SwapAllowlistExtension` gates on the router address instead of the real user, allowing any disallowed swapper to bypass the allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender` at the time `swap()` is called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any user who is not on the allowlist can bypass the gate by calling the router, which is a public, permissionless contract.

---

### Finding Description

`MetricOmmPool.swap()` captures `msg.sender` and passes it as the `sender` argument to `_beforeSwap()`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender, not the original user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` encodes this value and calls each extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap()` then checks this `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is the router. The extension receives `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]`.

There are two broken outcomes:
1. **Router not allowlisted:** Allowlisted users cannot use the router at all — the supported periphery path is broken for them.
2. **Router allowlisted (to fix outcome 1):** Every user, including those explicitly excluded from the allowlist, can swap by routing through the router. The allowlist is fully bypassed.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd users, institutional partners, or whitelisted bots) loses that restriction entirely. Any disallowed user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` and execute swaps on the restricted pool. This constitutes a direct policy bypass with fund-impacting consequences: unauthorized users can drain liquidity at oracle-quoted prices, extract spread fees, or manipulate pool state in ways the pool admin intended to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token, or setup is required to call it. Any user who observes that a pool has a swap allowlist can immediately attempt the bypass by routing through the router. The attack requires a single transaction and zero privileged access.

---

### Recommendation

The pool must pass the original user's address — not the pool's `msg.sender` — to the extension. One approach is to have the router forward the originating user address as part of `extensionData`, and have the extension decode and verify it. A cleaner approach is to add a `payer` or `originator` field to the swap interface that the router populates with `msg.sender` before calling the pool, and the pool forwards this to extensions as a separate argument distinct from `sender`. The extension should then gate on the originator, not the intermediate caller.

Alternatively, the pool can require that `sender == tx.origin` for direct calls, but this is fragile and incompatible with smart-contract wallets. The most robust fix is to thread the true user identity through the call stack explicitly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use router
  - Bob is NOT allowlisted

Attack:
  - Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes on the restricted pool

Result:
  - Bob, a disallowed user, successfully swaps on a curated pool
  - The allowlist provides zero protection against router-mediated swaps
```

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

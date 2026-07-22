### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router (a natural action to let allowlisted users use the router), every unprivileged user can bypass the allowlist by routing through it.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that forwarded `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

At this point `msg.sender` of `pool.swap()` is the **router**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass path:**

1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Pool admin allowlists the router (`setAllowedToSwap(pool, router, true)`) so that allowlisted users can use the router for multi-hop or convenience.
3. Any non-allowlisted user calls `router.exactInputSingle(...)` targeting that pool.
4. The extension sees `sender = router`, finds `allowedSwapper[pool][router] == true`, and passes.
5. The non-allowlisted user's swap executes successfully, bypassing the intended gate.

The router has no mechanism to forward the original caller's address into `pool.swap()`; the pool's `swap` signature does not accept a `sender` override parameter.

---

### Impact Explanation

The `SwapAllowlistExtension` invariant — *only allowlisted addresses may swap* — is silently broken for all router-mediated swaps whenever the router itself is allowlisted. Non-allowlisted users gain unrestricted swap access to a pool that was explicitly configured to restrict them. Depending on the pool's purpose (compliance, restricted LP, RWA), this constitutes an admin-boundary break with direct fund-flow consequences: unauthorized parties can drain token reserves via swaps the pool was designed to block.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected administrative action: allowlisted users need the router for multi-hop paths and for the `exactOutput` flow. A pool admin who allowlists the router to serve their legitimate users will unknowingly open the gate to all users. The trigger is a single, unprivileged `router.exactInputSingle` call — no special role, no front-running, no flash loan required.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the immediate `pool.swap()` caller. Two options:

1. **Check `recipient` instead of `sender`** if the intent is to restrict who receives output tokens (though this has its own limitations).
2. **Pass the original user through `extensionData`** and have the extension decode and verify it — but this requires the router to cooperate and is not enforced on-chain.
3. **Preferred:** Redesign `SwapAllowlistExtension.beforeSwap` to accept and verify a signed proof of allowlist membership embedded in `extensionData`, so the check is independent of the call stack depth. Alternatively, document that the extension is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pools that configure both a swap allowlist and a shared router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is KYC'd)
  allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)
  bob is NOT in allowedSwapper

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)  [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob receives tokens

Result:
  bob, a non-allowlisted user, successfully swaps in a pool
  that was configured to restrict swaps to allowlisted addresses only.
  The allowlist invariant is broken with zero privilege required.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

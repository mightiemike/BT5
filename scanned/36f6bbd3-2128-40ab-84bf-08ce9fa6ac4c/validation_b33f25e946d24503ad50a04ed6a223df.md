### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender`, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user — including those the admin intended to block — can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput`, `sender` is the router address, not the end user.

For router-mediated swaps to work at all on a curated pool, the admin must add the router to the allowlist: `allowedSwapper[pool][router] = true`. Once that entry exists, the check `allowedSwapper[pool][router]` passes for every user who routes through the router, regardless of whether that user was individually allowlisted or explicitly excluded.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that restriction entirely for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and execute a swap that the allowlist was supposed to block. This is a direct policy bypass on a production access-control extension, breaking the core invariant that only allowlisted actors may trade on the pool.

**Impact: High** — allowlist bypass on curated pools; disallowed users can trade and extract value from pools they should not be able to access.

---

### Likelihood Explanation

The router is the standard, publicly documented entry point for swaps. Any pool admin who wants to support normal user flows must allowlist the router. The bypass requires no special knowledge or privilege — any user simply calls the router. The condition (router allowlisted, individual users not) is the natural production configuration for a curated pool that still wants to support the periphery.

**Likelihood: High** — the trigger is a standard public call; the precondition is the expected admin configuration.

---

### Recommendation

The extension must gate on the actual end user, not the intermediary. The pool already passes the original `msg.sender` of the pool call as `sender`; the problem is that when the router is the caller, `sender` is the router. Two complementary fixes:

1. **In `SwapAllowlistExtension`**: check `sender` only when `sender` is not a known trusted router, or require the router to forward the original user identity in `extensionData` and decode it in the hook.

2. **Preferred — in `MetricOmmSimpleRouter`**: pass the original `msg.sender` (the end user) as the `sender` field in `extensionData` so the extension can recover the true actor. The extension then decodes the user from `extensionData` and checks `allowedSwapper[pool][user]`.

Minimal patch to the extension (requires router cooperation):

```solidity
function beforeSwap(
    address sender,
    address,
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata extensionData
) external view override returns (bytes4) {
    address actor = extensionData.length >= 20
        ? abi.decode(extensionData, (address))
        : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][actor]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` — necessary for router-mediated swaps.
3. Admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**.
7. Attacker's swap executes on the curated pool despite never being individually allowlisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

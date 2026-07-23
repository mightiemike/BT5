### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` gates pool swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks whether the **router** is allowlisted, not the **end user**. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for allowlisted users), every unprivileged caller can bypass the allowlist by routing through the public router contract.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` is the pool (the extension's caller). `sender` is the first argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call.

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` with `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` on `beforeSwap` and allowlists specific users: `allowedSwapper[pool][userA] = true`.
2. Admin also allowlists the router (`allowedSwapper[pool][router] = true`) so that allowlisted users can swap through the router.
3. Non-allowlisted `userC` calls `router.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → **passes**.
6. `userC` successfully swaps in the restricted pool, bypassing the allowlist entirely.

The router has no mechanism to restrict which end users can call it; it is a fully public contract.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's primary mechanism for curated pools (e.g., institutional-only, KYC-gated, or market-maker-restricted pools). Once the router is allowlisted, the guard is silently open to every caller. Non-allowlisted users can trade in pools that were designed to exclude them, breaking the pool admin's access-control boundary. LPs who deposited into a curated pool expecting restricted counterparties are exposed to unrestricted trading, which can cause direct LP value loss through adverse selection or front-running by actors the pool was designed to exclude.

This matches the allowed impact gate: **Admin-boundary break — factory/oracle role checks are bypassed by an unprivileged path.**

---

### Likelihood Explanation

**Medium.** The trigger requires the pool admin to allowlist the router, which is the natural and expected configuration step for any curated pool that also wants to support the official periphery router. The admin has no on-chain signal that allowlisting the router opens the pool to all callers; the extension's API gives no indication that `sender` is the router rather than the end user. The bypass is then reachable by any unprivileged address with no further preconditions.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor** (the end user), not the **transport layer** (the router). Two options:

1. **Require the end user's address in `extensionData`**: The router forwards the original `msg.sender` in `extensionData`; the extension verifies it against the allowlist. This requires a coordinated change to the router.
2. **Document that the router must never be allowlisted and that allowlisted users must call the pool directly**: This is a weaker mitigation and relies on admin awareness.

The cleaner fix is option 1, ensuring the checked identity is always the address that economically benefits from the swap.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension on beforeSwap
  admin calls swapExtension.setAllowedToSwap(pool, userA, true)
  admin calls swapExtension.setAllowedToSwap(pool, router, true)  // enable router for allowlisted users

Attack:
  userC (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: userC, zeroForOne: true, ...})

  Execution trace:
    router.exactInputSingle → pool.swap(msg.sender=router, ...)
    pool._beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap(sender=router)
      allowedSwapper[pool][router] == true  → no revert
    swap executes for userC

Result:
  userC receives token1 from the restricted pool.
  Direct pool call by userC would have reverted with NotAllowedToSwap.
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

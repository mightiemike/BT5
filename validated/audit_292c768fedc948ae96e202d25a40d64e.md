### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the value passed by the pool — which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the actual end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), every unprivileged address can bypass the allowlist by calling any of the router's `exact*` entry points.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← always the immediate caller of pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension as the `sender` parameter.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The router is `msg.sender` of that call, so the extension receives `sender = address(router)`. The allowlist lookup becomes `allowedSwapper[pool][router]`.

**Two broken outcomes result:**

1. **Allowlist bypass (critical path):** The pool admin must allowlist the router address to let any legitimate user swap through the router. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the actual end user is. Any unprivileged address can call `router.exactInputSingle()` and the guard passes.

2. **Legitimate users blocked:** If the pool admin does not allowlist the router (trying to gate individual addresses), every user who routes through the router is rejected even if their own address is explicitly allowlisted, because the extension sees `sender = router`, not `sender = user`.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, and to any other intermediary contract that calls `pool.swap()` on behalf of a user.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled accounts). The bypass allows any address to execute swaps against that pool's liquidity, defeating the access control entirely. Consequences include:

- Unauthorized parties extracting value from a restricted pool (e.g., arbitrage against a pool whose oracle spread is calibrated for trusted counterparties only).
- Protocol-level invariant broken: the pool admin's configured allowlist has no effect for router-mediated swaps, which is the primary user-facing entry point.

This is a direct loss-of-access-control impact on pool liquidity and fee revenue, meeting the "broken core pool functionality" and "admin-boundary break" criteria.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented in the protocol.
- Any user who discovers the bypass (or simply uses the router normally) triggers it without any privileged access.
- No special token behavior, malicious setup, or off-chain oracle manipulation is required.
- The bypass is unconditional once the router is allowlisted for any legitimate user.

Likelihood is **high**: the router is the standard path; the bypass is automatic.

---

### Recommendation

The extension must check the **actual end user**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user through the router.** Add a `recipient`/`originator` field to the swap call or use a transient-storage context (similar to how the router already stores callback context via `_setNextCallbackContext`) so the extension can read the true initiator. The pool would need to forward this value as a distinct parameter, or the extension would read it from a trusted registry.

2. **Check `recipient` instead of `sender` in the extension.** If the protocol's invariant is that the economic beneficiary of the swap is `recipient`, gate on `recipient`. This is already passed to the extension and is set by the user, not the router.

3. **Require direct pool interaction for allowlisted pools.** Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router; users must call `pool.swap()` directly. This is operationally fragile but avoids a code change.

Option 1 is the most robust.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that legitimate users can swap through the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams{
          pool: restrictedPool,
          recipient: attacker,
          ...
      })
  - router calls pool.swap(attacker, ...) → msg.sender of pool.swap() = router.
  - Pool calls extension.beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] → true (router was allowlisted).
  - Guard passes. Attacker's swap executes against the restricted pool.

Result:
  - attacker successfully swaps on a pool they are not allowlisted for.
  - The SwapAllowlistExtension guard is completely bypassed.
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

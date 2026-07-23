### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any Caller to Bypass Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller of `pool.swap()`, `sender` is the **router address**, not the actual end user. If the pool admin allowlists the router (the natural configuration for router-based trading), every unprivileged user can bypass the swap allowlist entirely by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

The `SwapAllowlistExtension` is designed to gate `swap` by swapper address, per pool:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct), and `sender` is the first argument passed by the pool — which is `msg.sender` of the `pool.swap()` call itself.

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to the extension:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
```

And in `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L88-99
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

When `MetricOmmSimpleRouter.exactInputSingle()` is used, the call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← checks router, not user
```

The pool admin who wants to support router-based trading for allowlisted users must allowlist the router address. But doing so grants every user — allowlisted or not — the ability to bypass the guard, because the extension sees only the router's address, not the originating user.

The `DepositAllowlistExtension` has a parallel but distinct wrong-actor binding: it ignores `sender` entirely and checks only `owner` (the position owner supplied by the caller). Since `addLiquidity` allows `msg.sender != owner`, any non-allowlisted address can call `pool.addLiquidity(allowlistedAddress, ...)` and pass the allowlist check — though the position is credited to the allowlisted address, limiting direct fund gain for the attacker.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` as a `beforeSwap` hook is intended to restrict trading to specific counterparties (e.g., institutional, KYC-gated, or curated-LP pools). Once the router is allowlisted:

- Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the curated pool.
- The extension sees `sender = router`, which is allowlisted, and passes.
- The unauthorized user executes a full swap against the pool's liquidity.

LP funds in the curated pool are exposed to unauthorized traders, breaking the core access-control invariant the extension was deployed to enforce. This maps directly to the allowed impact: **admin-boundary break where an unprivileged path bypasses a factory/pool role check**, and **broken core pool functionality causing loss of LP assets**.

---

### Likelihood Explanation

The scenario requires:
1. A pool deployed with `SwapAllowlistExtension` as `beforeSwap` hook — a supported, documented production extension.
2. The pool admin allowlisting the router address — the only way to allow allowlisted users to trade via the router, making it a natural and expected operational step.

Both conditions are part of the normal, non-malicious deployment and operation lifecycle. No privileged escalation or malicious setup is required from the attacker side; any user with token approval can exploit this once the router is allowlisted.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor** (the end user), not the immediate `pool.swap()` caller. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the end user, but this breaks for multi-hop or contract recipients.
3. **Require direct pool calls only**: Document that `SwapAllowlistExtension` is incompatible with router intermediaries and revert if `sender` is a known router address.

The cleanest fix is for the extension to accept an authenticated end-user address from `extensionData`, with the router being responsible for encoding `msg.sender` before forwarding the call.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  pool admin: setAllowedToSwap(pool, router, true)   // allowlist the router
  pool admin: setAllowedToSwap(pool, alice, true)    // allowlist alice
  bob = non-allowlisted user

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  → router calls pool.swap(bob, true, X, ...)
      msg.sender in pool = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      allowedSwapper[pool][router] == true  ✓ passes
  → swap executes for bob despite bob not being allowlisted

Result:
  bob successfully swaps against the curated pool,
  bypassing the intended access control.
  LP funds are exposed to unauthorized trading.
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

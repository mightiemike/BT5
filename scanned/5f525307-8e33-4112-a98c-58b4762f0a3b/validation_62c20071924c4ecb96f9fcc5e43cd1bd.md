### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Curated-Pool Swap Gate via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual end user. If the pool admin allowlists the router (required for any router-based swap to succeed), every user on the network can bypass the individual-swapper allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`msg.sender` here is the pool (the extension caller). `sender` is the value the pool passes as the first argument to `_beforeSwap`, which is always `msg.sender` of the `pool.swap()` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` to the pool is the **router address**, and the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For any user to swap through the router against a curated pool, the pool admin must allowlist the router address. Once the router is allowlisted, **every user** — including those explicitly not on the allowlist — can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension check passes unconditionally.

The same bypass applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: the pool admin intends to restrict which addresses can trade against LP liquidity (e.g., to exclude informed traders, MEV bots, or to comply with regulatory requirements). The bypass means:

- Any unprivileged user can swap against the curated pool's liquidity by routing through `MetricOmmSimpleRouter`.
- LP funds are exposed to toxic flow (adverse selection, MEV) that the allowlist was designed to prevent.
- The pool's core protection invariant — "only allowlisted addresses may swap" — is broken for all router-mediated paths.

This is a direct loss of LP principal through adverse selection on every unauthorized swap.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical, production-deployed periphery swap path.
- A pool admin who deploys a curated pool and wants legitimate allowlisted users to use the router (for multi-hop routing, slippage protection, deadline enforcement) **must** allowlist the router address — there is no other mechanism.
- Once the router is allowlisted, the bypass is available to any address with no special privilege.
- The pool admin cannot simultaneously support router-based swaps and enforce per-user access control through `SwapAllowlistExtension`.

---

### Recommendation

The extension must identify the **economic actor** (the end user), not the intermediary. Two approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router identity check (e.g., verify `msg.sender` is a factory-registered pool and that the pool's caller is a known router).

2. **Check `sender` only when `sender` is not a known router; otherwise require the router to attest the real user**: The extension maintains a registry of trusted routers and, when `sender` is a trusted router, reads the real user from a standardized field in `extensionData`.

3. **Simplest fix**: Document that `SwapAllowlistExtension` is incompatible with any shared router and must only be used with direct pool calls, and add a guard in the extension that reverts if `sender` is not an EOA or is a known router.

---

### Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E.
  - Pool admin allowlists router R: allowedSwapper[P][R] = true.
  - Alice (allowlisted EOA) and Bob (not allowlisted) both exist.

Attack (Bob bypasses allowlist):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
  2. Router calls P.swap(recipient=Bob, ..., extensionData).
  3. Pool calls _beforeSwap(msg.sender=Router, recipient=Bob, ...).
  4. Pool calls E.beforeSwap(sender=Router, ...).
  5. Extension checks: allowedSwapper[P][Router] == true → passes.
  6. Swap executes. Bob receives output tokens from LP liquidity.

Result: Bob, who is not on the allowlist, successfully swaps against the
curated pool's liquidity. The SwapAllowlistExtension protection is fully
bypassed for all router-mediated paths.
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

### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Allowing Any Caller to Bypass Per-User Swap Restriction — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals the pool's `msg.sender` — the router contract — not the actual end user. When the pool admin allowlists `MetricOmmSimpleRouter` so that router-mediated swaps are possible, every unprivileged user can bypass the per-user restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument forwarded by the pool, which the pool sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

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

The pool's `msg.sender` is the router, so `sender` forwarded to the extension is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

For any pool that has the router allowlisted (the only way to support router-mediated swaps on a restricted pool), the check passes for every caller regardless of their individual allowlist status.

---

### Impact Explanation

The `SwapAllowlistExtension` is a production access-control guard. Its purpose is to restrict which addresses may trade against a pool's LP liquidity. When bypassed via the router, any unprivileged address can execute swaps against a pool the admin intended to restrict. LP funds are exposed to counterparties the pool admin explicitly excluded. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or whitelist-only liquidity), this constitutes unauthorized access to LP principal and breaks the core pool functionality the extension was deployed to enforce.

---

### Likelihood Explanation

For a restricted pool to be usable through the standard periphery at all, the admin must allowlist the router. This is the natural and expected configuration — without it, no user can use the router on the pool. Once the router is allowlisted, the bypass is unconditionally available to every caller. No special privileges, flash loans, or unusual token behavior are required. Any user who knows the pool address can call `router.exactInputSingle` and trade.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: The router should forward the originating `msg.sender` as part of `extensionData` or a dedicated field, and the extension should decode and check that address.

2. **Check `sender` only when it is not a known router**: Alternatively, the pool or extension can maintain a registry of trusted routers and, when `sender` is a router, require the router to attest the real user identity in `extensionData`.

The simplest safe fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when the raw `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls router.exactInputSingle({pool: restrictedPool, ...})
  - router calls pool.swap(recipient, ...)
  - pool calls _beforeSwap(msg.sender=router, ...)
  - extension evaluates allowedSwapper[pool][router] → true
  - swap executes; attacker receives output tokens
  - allowlist never checked attacker's address
```

The attacker successfully swaps on a pool they are not individually allowlisted for, by routing through the publicly accessible `MetricOmmSimpleRouter`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of `pool.swap()` as `sender`. When any user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router address is allowlisted for a pool (the natural configuration for a pool that accepts router-mediated swaps), every unprivileged user can bypass the per-user swap allowlist by calling any of the router's `exact*` entry points.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the **router contract address**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router does not forward the original `msg.sender` (the end user) to the pool. Therefore, the extension sees `sender = router_address` and checks `allowedSwapper[pool][router_address]`.

A pool admin who wants to allow router-mediated swaps must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** call that arrives through the router, regardless of who the actual end user is. The per-user allowlist is completely bypassed.

The `DepositAllowlistExtension` does not have this problem because it gates the `owner` argument (the position owner), which the pool passes explicitly and which the `LiquidityAdder` sets to the actual beneficiary — not the adder contract itself.

---

### Impact Explanation

Any user — including those explicitly excluded from the allowlist — can execute swaps on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool's LP positions are exposed to unauthorized counterparties. LPs who deployed capital under the assumption that only allowlisted addresses could trade against them suffer unintended swap exposure. In an oracle-anchored pool the spread fee is the primary LP revenue; unauthorized swaps at unfavorable oracle moments (e.g., stale or wide spread) can extract value from LP reserves that the allowlist was meant to protect.

---

### Likelihood Explanation

The bypass requires only that the router address is allowlisted for the target pool, which is the expected production configuration for any pool that intends to support router-mediated swaps. The attacker needs no special role, no privileged access, and no prior state manipulation — a single public call to `MetricOmmSimpleRouter.exactInputSingle` is sufficient.

---

### Recommendation

Pass the original end-user address through the router to the pool, or redesign the allowlist check to gate the economically relevant actor. Two concrete options:

1. **Router forwards the real sender**: Add a `payer`/`originator` field to the swap call or use a transient-storage pattern (already used for callback context) so the pool can expose the true initiator to extensions.

2. **Extension reads transient context**: The router already stores the payer in transient storage (`_getPayer()`). The extension could read a standardized transient slot set by the router to obtain the real user address.

Either way, the `SwapAllowlistExtension` must check the address of the human/contract that initiated the swap, not the address of the intermediary router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so users can swap through it.
3. Pool admin does **not** allowlist `attacker`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → no revert.
8. Swap executes successfully for `attacker` despite being excluded from the allowlist.

The allowlist provides zero protection against any user who routes through the public router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

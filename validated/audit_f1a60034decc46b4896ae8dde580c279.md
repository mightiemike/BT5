### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user on the network can bypass the allowlist by calling the router.

---

### Finding Description

**Call chain that exposes the wrong identity:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap extension:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = pool's msg.sender
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is the **router**, not the user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput` (including the recursive callback hops in `_exactOutputIterateCallback`).

**The bypass:**

A pool admin who wants to allow router-mediated swaps must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for **every** user who calls the router, regardless of whether that user is individually allowlisted. The allowlist is fully neutralised for the router path.

**The alternative broken state:**

If the pool admin does not allowlist the router, individually allowlisted users cannot use the router at all — their swap reverts with `NotAllowedToSwap` because the router is not in the allowlist. There is no configuration that simultaneously (a) allows router-mediated swaps and (b) enforces per-user allowlist policy.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or compliance-gated participants) loses that restriction entirely for the router path. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the pool. This is a direct admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged public entrypoint. Depending on the pool's purpose, this can expose LP funds to unauthorized counterparties and break the pool's intended operating model.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin who wants to support normal UX must allowlist the router, which immediately opens the allowlist to all users. The trigger requires no special privileges, no flash loans, and no unusual token behavior — a single standard router call suffices.

---

### Recommendation

The extension must gate the **economically relevant actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires a convention between router and extension but preserves the existing interface.

2. **Check `recipient` instead of `sender` for single-hop swaps**: For `exactInputSingle`/`exactOutputSingle`, the recipient is the user-supplied address. This is not equivalent for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router-aware allowlist**: The extension stores a set of trusted routers and, when `sender` is a trusted router, reads the actual user from `extensionData` (injected by the router). This is the most robust approach.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router path
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: pool,
          recipient: attacker,
          ...
      })

Execution trace:
  1. Router.exactInputSingle → pool.swap(recipient=attacker, ...)
     msg.sender to pool = router
  2. pool._beforeSwap(sender=router, ...)
  3. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     checks: allowedSwapper[pool][router] == true  ✓  (admin set this)
  4. Swap executes — attacker receives tokens despite not being allowlisted

Result: attacker bypasses the swap allowlist entirely.
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

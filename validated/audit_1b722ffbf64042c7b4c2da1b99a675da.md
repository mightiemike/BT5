### Title
SwapAllowlistExtension Checks Router Address as Swapper Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool binds to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool. The extension therefore checks whether the **router** is allowlisted, not the actual user. If the pool admin allowlists the router (a natural action to enable periphery usage), every unprivileged user can bypass the curated-pool gate by routing through the router.

---

### Finding Description

**Binding chain:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap hook:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← bound to whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` uses that `sender` as the identity to gate:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← actual user address never forwarded
    );
```

The router stores the real user's address only in transient callback context (`_setNextCallbackContext`) for payment settlement; it is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The wrong-actor binding is structural:** the extension has no mechanism to recover the real user's address from the router call.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties). To allow those users to trade via the standard periphery, the admin must allowlist the router. The moment the router is allowlisted, the gate is open to every address on-chain: any caller can invoke `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and the extension will pass because `allowedSwapper[pool][router] == true`. The curated pool's entire access-control invariant collapses to a single shared router address, giving every unprivileged user the same swap rights as the intended allowlisted set.

Conversely, if the admin does not allowlist the router, allowlisted users cannot use the router at all, breaking the core swap flow for the intended participants.

Either outcome is fund-impacting: unauthorized traders drain LP value from a pool that was priced and configured for a restricted counterparty set.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap entry point documented and shipped with the protocol. Pool admins who configure a `SwapAllowlistExtension` will naturally allowlist the router to give their approved users access to the standard UX. The bypass requires no special privilege, no flash loan, and no unusual token behavior — only a standard `exactInputSingle` call through the public router. Any user who reads the pool's extension configuration can discover and exploit this immediately.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary. Two sound approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it. This requires the router to be trusted to supply the correct value, which is acceptable since the router is a protocol-controlled contract.

2. **Check `sender` only for direct pool calls; require `extensionData` attestation for router calls**: The extension can detect router-mediated calls (e.g., by checking whether `sender` is a known router) and require a signed or encoded user identity in `extensionData`.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the LP position recipient), which is explicitly supplied by the caller and is the economically correct identity to gate regardless of the intermediary.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension2)
  admin = pool admin
  alice = allowlisted user
  eve   = non-allowlisted attacker

Step 1 — Admin configures allowlist:
  swapExtension.setAllowedToSwap(pool, alice, true)
  // Intended: only alice can swap

Step 2 — Admin enables router for alice's convenience:
  swapExtension.setAllowedToSwap(pool, address(router), true)
  // Admin believes this lets alice use the router

Step 3 — Eve calls the router directly:
  router.exactInputSingle(ExactInputSingleParams{
      pool:      pool,
      recipient: eve,
      zeroForOne: true,
      amountIn:  X,
      ...
  })

Step 4 — Call chain:
  router → pool.swap(msg.sender=router)
  pool   → extension.beforeSwap(sender=router, ...)
  extension checks: allowedSwapper[pool][router] == true  ✓
  Swap executes. Eve receives tokens from the curated pool.

Result:
  Eve, who is not on the allowlist, successfully swaps on a curated pool.
  The allowlist invariant is broken. LP funds flow to an unauthorized counterparty.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

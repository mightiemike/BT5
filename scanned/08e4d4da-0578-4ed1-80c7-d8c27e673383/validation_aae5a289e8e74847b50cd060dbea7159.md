### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the **router address**, not the actual user. If the pool admin allowlists the router (the natural step to let allowlisted users reach the pool through the supported periphery path), every user — including non-allowlisted ones — can bypass the restriction by routing through the same contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
  )
);
```

That `sender` is set in `MetricOmmPool.swap` as `msg.sender` — the **direct caller of `pool.swap()`**:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller, not the originating user
  recipient,
  ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

`msg.sender` of `pool.swap()` is the **router**, so `sender` passed to `beforeSwap` is the router address. The allowlist check becomes:

```
allowedSwapper[pool][router]
```

**The actual user's address is never checked.**

This creates an irresolvable dilemma for the pool admin:

| Admin action | Result |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all — the check fails because `allowedSwapper[pool][router]` is false |
| **Allowlists the router** | Every user — allowlisted or not — can bypass the restriction by routing through the router |

The second branch is the exploitable path. A pool admin who wants their allowlisted users to access the pool through the supported periphery path must allowlist the router, which simultaneously opens the bypass to everyone.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` is a curated pool — it may be restricted to KYC'd counterparties, institutional LPs, or specific protocol integrations. Once the router is allowlisted (the only way to let legitimate users use the router), any address can swap in the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist guard fails completely open for all router-mediated swaps. Unauthorized users can extract value from a pool whose pricing or liquidity was calibrated for a specific, trusted set of counterparties.

**Severity: High** — the allowlist, a core pool protection, is fully bypassed for all router-mediated swaps once the router is allowlisted.

---

### Likelihood Explanation

The router is the primary user-facing swap entrypoint. Any pool admin who wants their allowlisted users to use the router must allowlist it. This is the expected operational path, not an edge case. The bypass is therefore reachable in any production deployment of a curated pool that supports router access.

**Likelihood: Medium** — requires the pool admin to allowlist the router, which is the natural operational step.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor** — the user who initiated the swap — not the intermediate contract that called `pool.swap()`. Two approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and the extension.
2. **Separate sender field**: Add a dedicated `originalSender` field to the `beforeSwap` hook signature that the pool populates from a transient-storage context set by the router before calling `pool.swap()`.

The `DepositAllowlistExtension` should be audited for the symmetric issue on the `addLiquidity` path via `MetricOmmPoolLiquidityAdder`.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist user A
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router so user A can use it
4. Non-allowlisted userB calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          restrictedPool,
           tokenIn:       token0,
           recipient:     userB,
           amountIn:      X,
           extensionData: ""
       })
5. Router calls restrictedPool.swap(userB, ..., "")
6. pool.swap sets sender = address(router)
7. beforeSwap checks allowedSwapper[pool][router] → true  ✓
8. Swap executes for userB — allowlist fully bypassed.
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

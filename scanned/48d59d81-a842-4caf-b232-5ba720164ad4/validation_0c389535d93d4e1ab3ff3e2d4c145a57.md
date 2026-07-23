### Title
SwapAllowlistExtension gates the router address instead of the economic actor, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. If the pool admin allowlists the router to enable router-mediated swaps, every non-allowlisted user can bypass the restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The pool therefore sees `msg.sender = router`. The extension receives `sender = router` and checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive failure modes:

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — broken core functionality |
| Router **allowlisted** | Every non-allowlisted user can bypass the restriction by routing through the router — full allowlist bypass |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

By contrast, `DepositAllowlistExtension` correctly gates the `owner` argument (the economic actor), not `sender` (the intermediary), so the deposit path does not share this flaw:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [5](#0-4) 

The swap path has no equivalent `owner`-style parameter that carries the original user identity through the router.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict swaps to specific participants (e.g., KYC-verified counterparties, institutional LPs) and also allowlists the router to support standard periphery usage inadvertently opens the pool to every user. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the curated pool, violating the intended access policy. This breaks the core allowlist functionality and constitutes a direct policy bypass on production curated pools.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. A pool admin who deploys a swap-allowlisted pool and wants allowlisted users to access it through the standard router will allowlist the router address — the natural and expected configuration. The bypass is then reachable by any unprivileged user with a single public call.

---

### Recommendation

The `beforeSwap` hook should gate the original economic actor, not the intermediary. Two viable approaches:

1. **Extension-data identity**: Require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and verify it. The extension must also verify that the caller is a trusted router (otherwise any caller can forge the identity).

2. **Recipient-based check**: Gate on `recipient` instead of `sender` when the pool is configured for router use, since the recipient is the address that economically benefits from the swap.

At minimum, the `SwapAllowlistExtension` NatSpec must document that `sender` is the direct caller of `pool.swap()`, not the originating user, so pool admins understand that allowlisting the router opens the pool to all users.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; admin allowlists Alice and the router.
2. Charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: charlie})
3. Router calls pool.swap(charlie, ...) → pool passes msg.sender=router to _beforeSwap.
4. Extension checks allowedSwapper[pool][router] == true → passes.
5. Charlie's swap executes on the curated pool, bypassing the intended restriction.
``` [6](#0-5) [1](#0-0) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

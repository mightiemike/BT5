### Title
`SwapAllowlistExtension` gates the router address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the end user's address. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user on the network, because the hook never inspects the actual originating account.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` directly from the pool's `swap` call-site, which is `msg.sender` of `pool.swap()`:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls `pool.swap(...)` directly, making the router the `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

Therefore, when a user swaps through the router, the hook evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who adds the router to the allowlist (the only way to permit router-mediated swaps on a curated pool) simultaneously grants every user on the network the ability to bypass the per-user gate.

The asymmetry with `DepositAllowlistExtension` is telling: that hook correctly ignores `sender` and checks `owner` (the LP position owner explicitly supplied by the caller), so the deposit gate is not affected. Only the swap gate is broken. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` and `allowAllSwappers = false` is intended to restrict trading to a curated set of counterparties (e.g., a private market-making pool, a KYC-gated venue, or a pool that excludes MEV bots). Once the router is allowlisted:

- Any unpermissioned user can call `router.exactInputSingle` / `exactInput` / `exactOutput` targeting the restricted pool and the hook passes.
- The unauthorized trader can execute swaps at oracle-derived prices, draining LP-owned token reserves through arbitrage or directional flow that the allowlist was specifically designed to prevent.
- The loss is direct and irrecoverable: LP principal leaves the pool in exchange for the input token at the oracle mid, with no recourse.

This matches the **Allowlist path** impact gate: "deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."

---

### Likelihood Explanation

The trigger condition is that the pool admin allowlists the router. This is the natural operational step any pool admin must take if they want to support router-mediated swaps for their allowlisted users — there is no other mechanism to do so. The admin cannot selectively allow specific users through the router; the only granularity available is the router address itself. The bypass is therefore reachable on any curated pool that supports the standard periphery router, which is the expected production configuration.

---

### Recommendation

The hook must verify the originating user, not the immediate caller. Two complementary approaches:

1. **Extension-data attestation**: Require the router to encode the originating `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension` decode and verify it. The router already threads per-hop `extensionData` through to the pool.

2. **Check `recipient` as a proxy**: For single-hop swaps the recipient is often the end user; however, this is not reliable for multi-hop or contract recipients.

The cleanest fix is approach 1: the router encodes `abi.encode(msg.sender)` into the swap's `extensionData`, and the extension decodes and checks that address instead of `sender`. This preserves the router's role as a trusted intermediary while restoring per-user gating.

---

### Proof of Concept

```
Setup:
  pool P has SwapAllowlistExtension E with allowAllSwappers[P] = false
  pool admin calls E.setAllowedToSwap(P, router, true)   // to enable router swaps
  pool admin calls E.setAllowedToSwap(P, alice, true)    // intended: only alice may swap

Attack (by bob, not allowlisted):
  bob calls router.exactInputSingle({
      pool: P,
      zeroForOne: true,
      amountIn: X,
      recipient: bob,
      ...
  })
  → router calls P.swap(bob, true, X, ...)   // msg.sender = router
  → pool calls E.beforeSwap(router, bob, ...)
  → hook checks allowedSwapper[P][router] == true  ✓  (passes)
  → bob's swap executes; LP funds transferred to bob

Result: bob, who was never allowlisted, swaps successfully in a pool
        that was supposed to be restricted to alice only.
``` [5](#0-4) [6](#0-5)

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

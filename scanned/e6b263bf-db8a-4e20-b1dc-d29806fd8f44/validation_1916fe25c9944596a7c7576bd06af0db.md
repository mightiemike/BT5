### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router — which is required for router-mediated swaps to function — the allowlist is bypassed for every user, including non-allowlisted ones.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (used as the mapping key, correct) and `sender` is the address the pool passes as the swap initiator. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // sender = whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly. The pool's `msg.sender` is the router, so the extension receives `sender = router`. The originating user's address is stored in transient storage only for the payment callback and is never forwarded to the pool or the extension:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

This creates an irreconcilable conflict for any pool admin who deploys a `SwapAllowlistExtension`:

- If the admin does **not** allowlist the router, allowlisted users cannot use the router at all — every router call reverts with `NotAllowedToSwap`.
- If the admin **does** allowlist the router (the natural step to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the allowlist by routing through the router, because the extension sees `allowedSwapper[pool][router] = true`.

The `extensionData` field passed through the router is ignored by `SwapAllowlistExtension` (the last `bytes calldata` parameter is unnamed and unused), so there is no existing mechanism to recover the originating user's identity. [5](#0-4) 

---

### Impact Explanation

Any user can bypass a pool's `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on a pool that has allowlisted the router. The allowlist — the pool admin's primary mechanism for restricting access (e.g., KYC, institutional-only pools, regulatory compliance) — is rendered ineffective. Non-allowlisted users can execute swaps at oracle-derived prices on a pool that was intended to be restricted, draining LP funds or violating the pool's access policy.

---

### Likelihood Explanation

The pool admin must allowlist the router for router-mediated swaps to work at all. This is a natural and expected configuration step for any production pool that wants to support the standard periphery. The admin is unlikely to realize that allowlisting the router grants unrestricted access to all users, since the extension's NatSpec states it "Gates `swap` by swapper address, per pool" — implying user-level granularity. The bypass requires only a standard router call, which is the most common user-facing entry point. [6](#0-5) 

---

### Recommendation

The `SwapAllowlistExtension` must check the originating user's address, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pool-level fix:** The pool should pass the originating user's address as a dedicated parameter to the extension hook (separate from `sender`, which is the immediate caller). The hook signature would carry both the immediate caller and the economic actor.
2. **Extension-level fix:** The router encodes the originating user's address in `extensionData`; the extension decodes and verifies it, while also verifying that `msg.sender` (the pool's caller) is a trusted router registered with the factory. This prevents spoofing while preserving user-level granularity.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `extension.beforeSwap(router, ...)` — `sender` = router.
7. Extension checks `allowedSwapper[pool][router]` = `true` → passes without revert.
8. Attacker's swap executes on the restricted pool, bypassing the allowlist entirely. [5](#0-4) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` parameter, which is the immediate `msg.sender` of `MetricOmmPool.swap()`. When users interact through `MetricOmmSimpleRouter`, the `sender` forwarded to the extension is the **router's address**, not the actual end-user. If the pool admin whitelists the router (the natural action to allow users to swap through the standard interface), every unprivileged user can bypass the allowlist entirely by routing through it.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — here `msg.sender` to the pool is the **router contract**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding the router's address as `sender`. [1](#0-0) 

4. `ExtensionCalling._beforeSwap()` encodes and dispatches `sender = router` to the extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`. [3](#0-2) 

The extension never sees the actual end-user address. The allowlist check is performed against the router, not the human caller.

**Two broken outcomes result:**

| Router allowlist state | Outcome |
|---|---|
| Router **not** whitelisted | All router users blocked, even individually whitelisted ones |
| Router **whitelisted** | **Every user** bypasses the allowlist — the guard is nullified |

The second case is the critical path. A pool admin who wants to allow their KYC'd or institutional users to swap through the standard router will whitelist the router address. This single entry in `allowedSwapper[pool][router]` opens the pool to the entire public.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, institutional partners, or whitelisted market makers). If the pool also expects users to interact via `MetricOmmSimpleRouter` — the primary user-facing interface — the admin must whitelist the router. Doing so silently removes all access control: any address can call `exactInputSingle` or `exactInput` on the router and execute swaps in the supposedly private pool.

Unauthorized swappers can drain LP-provided liquidity at the pool's oracle-derived bid/ask prices, causing direct loss of LP principal. The pool's entire security model collapses without any privileged action by the attacker.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap interface for the protocol. Any pool admin who configures a `SwapAllowlistExtension` and also wants their allowlisted users to use the router will inevitably whitelist the router, triggering the bypass. The attacker needs no special role, no tokens beyond the swap input, and no setup beyond calling the public router.

---

### Recommendation

The extension must verify the **actual end-user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the real user through `extensionData`**: The router should encode `msg.sender` into `extensionData` before calling the pool. The extension reads and verifies this value. This requires the extension to trust the router, so the router address must itself be validated (e.g., against a factory-registered router registry).

2. **Check `sender` only when called directly**: The extension can detect whether `sender` is a known router and, if so, require the real user to be encoded in `extensionData` and signed or verified.

The simplest safe fix is to remove router-level whitelisting entirely and require end-users to call `pool.swap()` directly when the allowlist extension is active, documenting this constraint clearly. Alternatively, the extension interface should be extended to carry a verified `realSender` field that the pool populates from a trusted source.

---

### Proof of Concept

```solidity
// Pool admin setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. Whitelist the router so legitimate users can swap:
swapAllowlist.setAllowedToSwap(pool, address(router), true);
// 3. Intend to block all non-KYC'd users

// Attacker (not KYC'd, not individually whitelisted):
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: largeAmount,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Passes: allowedSwapper[pool][router] == true
// Attacker swaps successfully, draining LP liquidity at oracle price
```

The check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router] == true`, so the revert is never triggered. [4](#0-3) [5](#0-4)

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

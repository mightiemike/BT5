### Title
SwapAllowlistExtension Bypass via Router — Any Unprivileged User Can Trade in Allowlist-Restricted Pools When the Router Is Allowlisted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router, so `sender = router`. If the pool admin allowlists the router to enable router-based swaps for legitimate users, the allowlist is silently bypassed for **all** users — any unprivileged address can trade in a curated, restricted pool by routing through the public router.

---

### Finding Description

**Actor binding mismatch in `SwapAllowlistExtension`**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

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

The pool receives `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The two broken states:**

| Router allowlisted? | Effect |
|---|---|
| Yes (to enable router swaps for legitimate users) | **Any user bypasses the allowlist** by routing through the public router |
| No | **All router-mediated swaps revert** even for legitimately allowlisted users — the allowlist and the router are mutually exclusive |

The same bypass applies to `exactInput` (multi-hop) and `exactOutput` (recursive callback), since all paths call `pool.swap()` from the router contract.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to specific counterparties (e.g., KYC-gated users, institutional market makers, or whitelisted addresses). If the pool admin allowlists the router to enable router-based swaps for those counterparties, the allowlist is rendered ineffective: any unprivileged user can call `router.exactInputSingle()` and trade in the restricted pool. The pool's oracle-anchored pricing means the attacker receives tokens at the oracle mid-price minus spread, directly extracting value from LP positions that were placed under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal and protocol fees above Sherlock thresholds.

---

### Likelihood Explanation

The scenario is highly likely in practice:
1. Pool admins who deploy a swap-allowlisted pool and want their allowlisted users to use the standard router will naturally allowlist the router address.
2. The router is a public, permissionless contract — any user can call it.
3. No special setup or privileged access is required for the attacker; a single `exactInputSingle` call suffices.
4. The extension's documentation says it "Gates `swap` by swapper address" — admins have no reason to suspect the router collapses all user identities into one address.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economically relevant actor** — the end user — not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention and is fragile.

2. **Check `sender` against the allowlist and also accept the router as a transparent forwarder only if the router itself verifies the original user**: The router would need to encode the original caller and the extension would verify a signed or trusted forwarding claim.

3. **Preferred — gate on `sender` and require direct pool calls for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router; allowlisted users must call `pool.swap()` directly. Add a check in the extension or router that reverts if the router is used on an allowlist-restricted pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps for legitimate users
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, tokenIn: token0, ...})
  2. router calls pool.swap(recipient=bob, ...)
  3. pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...) checks allowedSwapper[pool][router] == true → passes
  5. bob's swap executes at oracle price, extracting value from LP positions
  6. bob was never allowlisted; the guard was silently bypassed

Verification:
  - Direct call: bob calls pool.swap() directly → allowedSwapper[pool][bob] == false → reverts NotAllowedToSwap ✓
  - Router call: bob calls router.exactInputSingle() → allowedSwapper[pool][router] == true → succeeds ✗
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

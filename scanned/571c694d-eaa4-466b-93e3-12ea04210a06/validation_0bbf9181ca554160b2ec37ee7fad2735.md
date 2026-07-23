### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Replaces User Identity in `beforeSwap` Sender Check - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router address (the only way to let allowlisted users trade via the router), every user — including non-allowlisted ones — can bypass the swap restriction by routing through the router.

---

### Finding Description

**Call chain establishing the wrong-actor binding:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap(), not the originating user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
    sender,   // ← still the direct pool caller
    ...
  ))
);
```

`SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`.

**The router always appears as `sender`:**

Every `MetricOmmSimpleRouter` entry point calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80  (exactInputSingle)
IMetricOmmPoolActions(params.pool).swap(
  params.recipient, params.zeroForOne, ..., params.extensionData
);
// msg.sender seen by the pool = address(router)
```

The same holds for `exactOutputSingle`, `exactInput` (all hops), and `exactOutput` (all hops including the recursive callback path). In every case the pool's `msg.sender` — and therefore the `sender` the extension checks — is the router contract address, not the originating EOA.

**The forced dilemma:**

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ Reverts (router not in list) | ❌ Reverts |
| Yes | ✅ Passes | ✅ **Passes — bypass** |

To let any allowlisted user trade through the router, the pool admin must add `allowedSwapper[pool][router] = true`. That single entry then grants every user on-chain access to the pool through the router, defeating the allowlist entirely.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional partners, or whitelisted market makers) loses that restriction entirely for router-mediated swaps. Any non-allowlisted user can execute swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the pool admin intended to reserve for approved parties. Because `MetricOmmSimpleRouter` is the standard user-facing entry point, the bypass is trivially reachable by any on-chain actor.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loans, and no oracle manipulation. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted (both facts are readable on-chain) can immediately exploit it. Pool admins who want allowlisted users to use the router have no alternative but to allowlist the router, making the bypass a near-certain consequence of any real-world deployment that combines the allowlist extension with the router.

---

### Recommendation

The extension must resolve the originating user rather than the direct pool caller. Two sound approaches:

1. **Pass the originating user through the router.** Add an optional `originSender` field to the extension payload that the router populates with `msg.sender` before calling the pool. The extension reads and verifies this field instead of the raw `sender` argument.

2. **Check `recipient` instead of `sender` for swap gating.** If the pool's design intent is to restrict who *receives* output, gate on `recipient`. If the intent is to restrict who *initiates* the swap, the router must propagate the true initiator.

3. **Restrict the router from calling allowlisted pools.** The extension can revert when `sender` is a known router address and the originating user is not separately verified.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowedSwapper[pool][alice]  = true   (intended allowed user)
  router → allowedSwapper[pool][router] = true   (added so alice can use the router)

Attack (charlie, not allowlisted):
  charlie calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
      → pool calls _beforeSwap(msg.sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  → PASSES
    → swap executes, charlie receives output tokens

Result: charlie bypasses the swap allowlist with zero special access.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

### Title
`SwapAllowlistExtension` gates the router address instead of the originating EOA, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of the pool's `swap` function. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating EOA. If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for their allowlisted users), every user — including those not individually allowlisted — can bypass the restriction by calling any router entry-point.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is the sole enforcement point for the per-pool swap allowlist:

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

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the argument the pool passes — which is `msg.sender` of the pool's own `swap` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The pool's `msg.sender` is the router, so `sender` passed to the extension is the **router address**, not the originating EOA. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable identity mismatch:

| Admin intent | Admin action | Actual result |
|---|---|---|
| Allow specific users via router | Allowlist router address | **All users** can swap via router |
| Allow specific users directly only | Allowlist individual EOAs | Allowlisted users **cannot** use the router |

A pool admin who wants their allowlisted users to be able to use the official router will allowlist the router address. At that point, every non-allowlisted user can bypass the restriction by routing through `MetricOmmSimpleRouter`.

The same structural flaw applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also call `pool.swap` with `msg.sender = router`. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or regulatory-compliant participants) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps at the oracle-anchored bid/ask, draining pool liquidity and collecting output tokens they were explicitly barred from receiving. This is a direct bypass of an admin-configured access control with fund-impacting consequences.

**Severity: Medium** — Impact is direct (non-allowlisted users receive swap output from a restricted pool), but requires the pool admin to have allowlisted the router address, which is a plausible but not universal configuration.

---

### Likelihood Explanation

The scenario is plausible in any deployment where:
1. A pool is configured with `SwapAllowlistExtension` to restrict swaps.
2. The pool admin allowlists the router so that their approved users can interact via the standard periphery interface.

A pool admin who understands the allowlist as "gate individual users" would naturally allowlist the router to give those users a convenient entry point, without realizing that the extension checks the router's address rather than the originating EOA. The `generate_scanned_questions.py` research file explicitly flags this path as a high-priority audit target, confirming the design tension is real. [5](#0-4) 

---

### Recommendation

The extension must recover the originating user identity rather than relying on the immediate pool caller. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a trusted encoding convention.

2. **Check `sender` against a per-pool router registry**: The extension maintains a mapping of trusted routers per pool. When `sender` is a known router, the extension reads the originating user from a transient-storage slot set by the router before the pool call, and checks that address against the allowlist.

3. **Restrict allowlisting to EOAs only**: Document and enforce (via `extcodesize` check) that only EOA addresses may be added to `allowedSwapper`, preventing the router from being allowlisted.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin allowlists router R: allowedSwapper[P][R] = true
  user Alice (EOA) is individually allowlisted: allowedSwapper[P][Alice] = true
  user Bob (EOA) is NOT allowlisted

Attack:
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient=Bob, ...)
    → pool calls E.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[P][router] == true  ✓
    → swap proceeds; Bob receives output tokens

Expected: Bob's swap should revert with NotAllowedToSwap()
Actual:   Bob's swap succeeds because the router is allowlisted
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

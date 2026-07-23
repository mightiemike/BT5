### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Substitutes for True Swapper Identity - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates a swap, `sender` becomes the **router address**, not the originating user. If the router is allowlisted for a pool (the natural configuration to enable router-mediated swaps for allowlisted users), every user — including those explicitly not on the allowlist — can bypass the guard by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct). `sender` is whatever `msg.sender` was when `pool.swap()` was called.

In `MetricOmmPool.swap`, the value forwarded to `_beforeSwap` as `sender` is `msg.sender` of the swap call: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the router is `msg.sender`: [3](#0-2) 

So `sender` arriving at the extension is the **router contract address**, not the originating user. The extension has no visibility into who called the router.

The pool admin who wants to allow router-mediated swaps for their allowlisted users must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once that entry exists, **any** address that calls the router targeting that pool passes the guard, because the extension sees `sender = router` and finds it allowlisted. The per-user restriction is completely nullified.

The `generate_scanned_questions.py` audit target explicitly flags this path: [4](#0-3) 

> "Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any unprivileged user can:

1. Call `MetricOmmSimpleRouter.exactInputSingle` pointing at the restricted pool.
2. The router becomes `sender`; the extension approves it.
3. The unauthorized user executes a full swap against the pool's liquidity.

LPs in the restricted pool suffer impermanent loss and fee dilution from unauthorized swaps they never consented to. If the pool was priced for a specific participant set (e.g., tighter spreads for trusted counterparties), adversarial arbitrageurs can extract value continuously. This is a direct loss of LP principal and owed fees — matching the "broken core pool functionality causing loss of funds" impact criterion.

---

### Likelihood Explanation

- The router is a public, permissionless contract. Any address can call it.
- The pool admin must allowlist the router to make the extension usable for router-mediated swaps by legitimate users. This is the expected operational configuration.
- Once the router is allowlisted, the bypass requires zero privilege and zero special setup — a single `exactInputSingle` call suffices.
- The bypass is permanent until the admin removes the router from the allowlist (which also breaks legitimate router usage).

---

### Recommendation

The extension must verify the **originating user**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the true initiator through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires the router to be trusted to populate the field honestly — acceptable if the router is a known, immutable contract.

2. **Check `sender` against the allowlist and treat the router as transparent**: Require that when `sender` is a known router, the extension also receives and verifies the real user identity from `extensionData`. Reject calls where `sender` is the router but no valid user identity is provided.

Either way, the extension must never grant access based solely on the router's address being allowlisted.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only intended swapper
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...}) // bob is msg.sender to router

  router calls:
    pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    // msg.sender to pool = router

  pool calls:
    extension.beforeSwap(sender=router, ...)
    // allowedSwapper[pool][router] == true → passes

  Result: bob swaps successfully against the restricted pool.
          The allowlist check on alice vs. bob is never performed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

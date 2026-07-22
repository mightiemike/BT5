### Title
`SwapAllowlistExtension` Gates on the Direct Pool Caller (`sender`) Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end user. If the pool admin allowlists the router (the natural step to enable the standard periphery path), every unpermissioned user can bypass the per-user swap allowlist by calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument, which the pool sets to `msg.sender` of the `pool.swap()` call:

```solidity
// MetricOmmPool.sol – pool.swap()
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

The extension then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

So `sender` delivered to the extension is the **router address**, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants to allow specific users to trade via the standard periphery must allowlist the router. Once the router is allowlisted, `allowAllSwappers[pool]` is false but `allowedSwapper[pool][router]` is true, so the check passes for **any** caller of the router — including users who are explicitly not on the allowlist.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths, all of which call `pool.swap()` with `msg.sender = router`. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position owner passed explicitly), which is not substituted by the liquidity adder address, so the deposit path does not share this flaw. [5](#0-4) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties or institutional LPs). The allowlist is entirely bypassed by any unpermissioned user who routes through `MetricOmmSimpleRouter`. The attacker can drain LP-provided liquidity at oracle prices without being on the allowlist, defeating the pool's curation policy and causing direct loss of LP assets to unauthorized flow.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery swap path. Any pool admin who enables the allowlist extension and also wants users to use the router will naturally allowlist the router address. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to a public router function.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **end user** rather than the direct pool caller. Two options:

1. **Check `recipient` instead of `sender`** — but `recipient` is the output receiver, not the payer, so this is also wrong for multi-hop paths.
2. **Pass the original `msg.sender` through the router as `extensionData`** and have the extension decode and verify it — but this is forgeable by any caller.
3. **Correct fix:** The pool should expose a way for the router to forward the original initiator, or the extension should check `sender` and require that any allowlisted intermediary (router) is not permitted — i.e., only EOA/direct callers are accepted. Alternatively, the allowlist should be keyed on `recipient` for swap output ownership, or the router should be prohibited from being allowlisted and users must call the pool directly.

The cleanest fix is to remove the ability to allowlist contract intermediaries and require `sender` to be an EOA, or to redesign the extension to accept a signed proof of the original initiator forwarded through `extensionData`.

---

### Proof of Concept

```
Setup:
  1. Pool admin deploys pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     — intending to let allowlisted users use the standard periphery.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. Attacker (not on allowlist) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})
  5. Router calls pool.swap(recipient, ...) with msg.sender = router.
  6. Pool calls extension.beforeSwap(sender=router, ...).
  7. Extension checks allowedSwapper[pool][router] == true → passes.
  8. Swap executes. Attacker receives output tokens.
  9. Allowlist is bypassed with zero special privileges.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

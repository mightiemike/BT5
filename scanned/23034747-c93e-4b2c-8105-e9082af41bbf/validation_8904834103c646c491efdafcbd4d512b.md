### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller, so the hook evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces two fund-impacting failure modes: (a) allowlisted EOAs are silently blocked from using the supported periphery path, and (b) if the pool admin allowlists the router to restore access, every user — including non-allowlisted ones — can bypass the guard entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the router contract, not the originating EOA. The hook therefore evaluates:

```
allowedSwapper[pool][router]   // checked
allowedSwapper[pool][user_eoa] // never checked
```

**Failure mode A — false blocking**: The pool admin allowlists specific EOAs (`allowedSwapper[pool][alice] = true`). Alice calls the router; the hook sees the router address, finds it not allowlisted, and reverts `NotAllowedToSwap`. Alice is a valid, allowlisted swapper but cannot use the supported periphery path.

**Failure mode B — full bypass**: To fix mode A, the pool admin allowlists the router (`allowedSwapper[pool][router] = true`). Now every user — including Bob who was never allowlisted — can call `router.exactInputSingle` and the hook passes unconditionally, because the router is allowlisted. The allowlist is completely neutralised for all router-mediated swaps.

The `DepositAllowlistExtension` does not share this flaw because it gates on the `owner` parameter (the position owner), not `sender`: [5](#0-4) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The router is a public, permissionless contract. Any non-allowlisted address can execute swaps against the pool, draining LP value at oracle-anchored prices without the pool admin's consent. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed alongside the protocol. Pool admins who configure a swap allowlist will naturally expect it to apply to all swap paths, including the router. The bypass requires no special privilege — any EOA can call the router. The false-blocking mode (A) will surface immediately in testing, creating strong pressure on the pool admin to allowlist the router, which triggers mode B. The path from deployment to full bypass is therefore a single, predictable admin action.

---

### Recommendation

The hook must resolve the originating user, not the direct pool caller. Two options:

1. **Pass `tx.origin` as an additional argument** — rejected because it breaks contract-wallet and smart-account use cases.

2. **Require the router to forward the real swapper identity in `extensionData`** and have the hook decode it, verifying that `msg.sender` (the pool) is a known pool and that the decoded identity matches the payer stored in the router's transient context.

3. **Preferred — gate on `msg.sender` inside the router before calling the pool**: add an `isAllowedToSwap(pool, msg.sender)` pre-check in `MetricOmmSimpleRouter.exactInputSingle` (and all `exact*` variants) that reads the extension's allowlist directly, reverting before the pool call if the originating user is not permitted. The hook then remains as a backstop for direct pool calls.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only permitted swapper
  allowedSwapper[pool][router] = true  // admin adds this to let alice use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})
      → pool.swap(bob, ...) with msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓ passes
      → swap executes at oracle price, bob receives token output
      → pool LP value reduced without admin consent

Result:
  bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist guard is completely bypassed for all router-mediated swaps.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

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

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to gate pool swaps by per-user address. However, the `sender` value it inspects is `msg.sender` from the pool's perspective — the immediate caller of `pool.swap()`. When users interact through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. A pool admin who allowlists the router (the natural action to let users use the router) inadvertently opens the gate to every user on-chain, completely defeating the per-user access control.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value and forwards it to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks that `sender` against its allowlist.**

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

Here `msg.sender` is the pool, and `sender` is whoever called `pool.swap()`.

**Step 3 — MetricOmmSimpleRouter is the immediate caller of `pool.swap()`.**

In `exactInputSingle`, the router calls the pool directly: [4](#0-3) 

So from the pool's perspective, `msg.sender` = router address, and that is the `sender` value the extension receives.

**The mismatch:** The pool admin intends to allowlist specific end-users (e.g., KYC'd traders). To let those users use the router, the admin also allowlists the router address via `setAllowedToSwap(pool, router, true)`. At that point `allowedSwapper[pool][router] = true`, and the check in `beforeSwap` passes for **every** user who calls through the router — the actual end-user identity is never inspected.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for private/gated access (e.g., institutional-only, KYC-gated, or partner-only liquidity) loses all access control the moment the router is allowlisted. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps against the pool. LPs in such a pool suffer adverse selection from counterparties they explicitly excluded, resulting in direct loss of LP principal through unfavorable trades.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing entry point for swaps. A pool admin who deploys a gated pool and wants their permitted users to have a normal UX will naturally allowlist the router. The documentation for `SwapAllowlistExtension` says it "gates `swap` by swapper address" without clarifying that the swapper is the immediate pool caller, not the end user. The misconfiguration is therefore a predictable, non-adversarial mistake by a legitimate pool admin.

---

### Recommendation

The `SwapAllowlistExtension` must resolve the true end-user identity rather than the immediate pool caller. Two viable approaches:

1. **`extensionData` signature**: Require the end-user to sign a permit that is passed through `extensionData`; the extension verifies the signature and checks the recovered address against the allowlist.
2. **Router-level enforcement**: The router exposes the real `msg.sender` via a dedicated field in `extensionData`, and the extension trusts only calls from a known router that attests the real sender.

Additionally, the `DepositAllowlistExtension` has a symmetric issue: it checks `owner` (the position beneficiary supplied by the caller) rather than `sender` (the actual token provider), allowing any address to deposit tokens on behalf of an allowlisted owner and bypass the depositor gate. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: setAllowedToSwap(pool, userA, true)       // permit userA
  pool admin calls: setAllowedToSwap(pool, router, true)      // permit router so userA can use it

Attack:
  userB (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (check passes)
      → swap executes, tokens transferred to userB
```

`userB` successfully swaps against the gated pool. The per-user allowlist is completely bypassed. LP funds are exposed to the unauthorized counterparty. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

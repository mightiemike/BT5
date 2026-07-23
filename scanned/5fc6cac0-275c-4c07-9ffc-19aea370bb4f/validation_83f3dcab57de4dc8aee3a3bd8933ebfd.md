### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. The extension therefore checks the router's allowlist entry, not the user's. If the router is allowlisted (a natural admin choice for "allow all router users"), every user — including non-allowlisted ones — bypasses the allowlist entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
              [msg.sender = router]
         → MetricOmmPool.swap() passes msg.sender as `sender` to _beforeSwap()
         → ExtensionCalling._beforeSwap(sender=router, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address, not original user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap()` without forwarding the original user's identity: [4](#0-3) 

**Two broken invariants result:**

1. **Bypass (High):** A pool admin who allowlists the router address (intending "allow all official-router users") inadvertently allows *every* user — including non-allowlisted ones — to swap on the curated pool. The allowlist is completely ineffective for router-mediated swaps.

2. **Blocking (Medium):** A pool admin who allowlists specific user addresses finds those users cannot swap through the router (the router's address fails the check), breaking the primary user-facing entry point.

The `DepositAllowlistExtension` does **not** share this flaw — it correctly checks `owner` (the position beneficiary), which the pool passes explicitly and which the liquidity adder preserves: [5](#0-4) 

The swap allowlist has no equivalent protection.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to trusted counterparties loses that protection entirely for router-mediated swaps when the router is allowlisted. Any user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` and swap against the pool, extracting value from LPs at oracle-driven prices the pool admin intended to reserve for specific parties. This is a direct loss of LP principal and protocol fee revenue attributable to unauthorized swap execution — matching the "admin-boundary break" and "broken core pool functionality causing loss of funds" impact categories.

---

### Likelihood Explanation

The `SwapAllowlistExtension` NatSpec states it "Gates `swap` by swapper address, per pool." A pool admin who wants to allow all users of the official router will naturally call `setAllowedToSwap(pool, router, true)`, not realizing this opens the gate to everyone. The router is a public, permissionless contract. The mistake is easy to make and the bypass requires no special privilege — any EOA can call the router. [6](#0-5) 

---

### Recommendation

**Short term:** In `SwapAllowlistExtension.beforeSwap`, check `recipient` (the economic beneficiary of the swap output) rather than `sender`, or require the router to encode the original user's address in `extensionData` and decode it in the extension.

**Long term:** Define a canonical "originator" field in the extension call signature (analogous to how `addLiquidity` separates `sender` from `owner`) so every extension can gate on the economically relevant actor regardless of which periphery contract initiates the pool call. Document that `sender` in `beforeSwap` is the immediate pool caller, not the end user, so extension authors do not conflate the two.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to allow "official router users."
3. Non-allowlisted Eve calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Pool receives `msg.sender = router`; extension checks `allowedSwapper[pool][router]` → `true`.
5. Eve's swap executes successfully despite never being individually allowlisted.
6. Repeat with any number of non-allowlisted addresses — all bypass the allowlist via the router.

Contrast: Eve calling `pool.swap(...)` directly would check `allowedSwapper[pool][eve]` → `false` → revert. The router path is the bypass vector. [3](#0-2) [7](#0-6) [8](#0-7)

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

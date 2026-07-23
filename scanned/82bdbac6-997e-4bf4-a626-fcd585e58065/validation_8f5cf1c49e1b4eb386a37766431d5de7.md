### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real User — Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual end user. The extension therefore checks whether the **router** is allowlisted, not the human trader. If the pool admin allowlists the router (the natural thing to do so that legitimate users can use the official periphery), every unpermissioned address can bypass the allowlist by routing through it.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), i.e. the router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the path is:

```
User → MetricOmmSimpleRouter.exactInputSingle() → pool.swap()
```

the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`. The actual user's address is never consulted.

A pool admin who wants allowlisted users to be able to use the official router must add the router to the allowlist. The moment they do, **every address on the network** can bypass the allowlist by calling any of the router's public entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC'd users, protocol partners, or whitelisted strategies). Once the router is allowlisted — which is required for any allowlisted user to trade through the standard periphery — the restriction is nullified for every address. Any user can execute swaps at the oracle-derived bid/ask price, draining LP value or front-running in a pool that was designed to be closed to them. This is a direct, unconditional bypass of the pool's primary access-control guard with no additional preconditions beyond using the public router.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who deploy a curated pool and want their allowlisted users to trade through the router must add the router to the allowlist. This is the expected operational pattern; the alternative (requiring every allowlisted user to call `pool.swap()` directly and implement `IMetricOmmSwapCallback`) is impractical. Therefore the precondition — router is allowlisted — is the normal production configuration, making the bypass reachable by any unpermissioned user with no special privileges. [4](#0-3) [5](#0-4) 

---

### Recommendation

The extension must gate the **original human caller**, not the intermediary. Two complementary fixes:

1. **Pass the original caller through the router.** The router already tracks the real payer in transient storage (`_getPayer()`). Thread the original `msg.sender` through `extensionData` or a dedicated field so the pool can forward it to extensions as a verified "originator" argument.

2. **Check `recipient` or a dedicated originator field in the extension.** Alternatively, redesign `beforeSwap` to accept an explicit originator address that the pool populates from a trusted transient context set by the router, rather than relying on the raw `sender` (direct pool caller).

Until fixed, pool admins should **not** add the router to the swap allowlist and should require allowlisted users to call `pool.swap()` directly.

---

### Proof of Concept

```solidity
// Setup:
// 1. Pool deployed with SwapAllowlistExtension
// 2. Admin allowlists the router: extension.setAllowedToSwap(pool, address(router), true)
// 3. Attacker (not in allowlist) calls:

router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         token0,
        recipient:       attacker,
        zeroForOne:      true,
        amountIn:        1e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);

// pool.swap() is called with msg.sender = router
// _beforeSwap(sender=router, ...) is dispatched
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// Swap executes; attacker receives token1 output
// allowedSwapper[pool][attacker] was never consulted
``` [6](#0-5) [7](#0-6) [5](#0-4) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

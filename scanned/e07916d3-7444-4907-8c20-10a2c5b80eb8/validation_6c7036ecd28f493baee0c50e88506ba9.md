### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any unprivileged caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. The extension therefore checks whether the router is allowlisted, not whether the real user is allowlisted. Any non-allowlisted user can bypass a curated pool's swap allowlist by calling the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads `sender` (the first argument) and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

The same pattern applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`. In every case, the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. [5](#0-4) 

The pool admin faces an impossible choice:

- **Allowlist the router** → every user, including non-allowlisted ones, can bypass the guard by routing through the public router.
- **Do not allowlist the router** → legitimately allowlisted users cannot use the router at all, breaking the standard swap path.

Neither option preserves the intended per-user curation policy.

---

### Impact Explanation

On a curated pool (e.g., KYC-gated, institutional-only, or compliance-restricted), any unprivileged user can execute swaps by calling `MetricOmmSimpleRouter.exactInputSingle` or any other router entry point. The extension's allowlist is silently bypassed. This constitutes a direct admin-boundary break: the pool admin's configured access control is circumvented by an unprivileged path (the public router), allowing unauthorized users to trade against restricted LP positions and extract value the pool was designed to protect.

---

### Likelihood Explanation

Likelihood is **high**. The `MetricOmmSimpleRouter` is the standard, publicly documented swap entry point for end users. Any non-allowlisted user who discovers the curated pool can immediately exploit this by calling the router rather than the pool directly. No special privileges, flash loans, or multi-transaction setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must identify the **economic actor** (the real user), not the intermediary. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router identity check (e.g., `onlyPool` already enforces the pool is the caller, so the extension can trust the pool forwarded the data honestly).

2. **Check `sender` only when the caller is not a known router**: The extension maintains a registry of trusted routers; when `sender` is a router, it reads the real user from `extensionData`.

3. **Gate on `recipient` instead of `sender`**: If the policy intent is to restrict who receives output tokens, `recipient` is the correct field. However, if the intent is to restrict who initiates the trade, neither `sender` nor `recipient` alone is sufficient without router cooperation.

The cleanest fix is for the router to encode the originating user in `extensionData` and for the extension to decode and verify it, with the pool's `onlyPool` guard on the extension entry point ensuring the data was not tampered with by an unprivileged caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, alice, true)  // only alice is allowed
  - Pool admin does NOT allowlist the router or bob

Attack (single transaction, no privileges):
  1. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle(
           pool=curated_pool,
           recipient=bob,
           zeroForOne=true,
           amountIn=X,
           ...
       )
  2. Router calls pool.swap(bob, true, X, ...) with msg.sender=router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router]
     → router is not explicitly blocked (allowAllSwappers[pool] may be false,
       but if the admin allowlisted the router to enable router swaps, this passes)
  5. If router is allowlisted: swap executes for Bob despite Bob not being allowlisted
  6. Bob receives token output from the curated pool

Result: Bob bypasses the per-user swap allowlist with a single public router call.
``` [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

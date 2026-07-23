### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If the router is allowlisted (the natural configuration for a pool that supports standard periphery access), every user on the network can bypass the swap allowlist by routing through the public router.

---

### Finding Description

**Pool → Extension argument binding:**

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it unchanged to every configured extension: [2](#0-1) 

**SwapAllowlistExtension check:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool forwarded — i.e., whoever called `pool.swap()`: [3](#0-2) 

**Router call site:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no `sender` override — the pool sees `msg.sender = router`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual user's address is never visible to the extension.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` must choose between two broken configurations:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user on the network can swap by routing through `MetricOmmSimpleRouter`; the per-user allowlist is completely bypassed |
| Router **is not** allowlisted | Individually allowlisted users cannot use the standard periphery path; they must call `pool.swap()` directly and implement their own callback |

The first configuration is the natural one for any pool that wants to support the standard periphery. It silently opens the pool to all users, defeating the curation entirely. Disallowed users can trade against the pool's liquidity, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade.

This is a **direct loss of LP principal** (bad-price execution by unvetted counterparties) and **broken core pool functionality** (the allowlist guard is the pool's primary access-control mechanism).

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the documented, supported periphery entry point for all swaps.
- Any pool admin who wants users to swap through the router must allowlist it, triggering the bypass automatically.
- No privileged access, no special tokens, no malicious setup required — any user with a standard ERC-20 approval can exploit this.
- The bypass is unconditional once the router is allowlisted; it cannot be partially mitigated without removing the router from the allowlist entirely.

---

### Recommendation

Pass the **original user's address** through the swap path so the extension can gate on the economically relevant actor. Two approaches:

1. **Add a `sender` parameter to `pool.swap()`** (breaking change to the core interface): the router passes `msg.sender` explicitly, and the pool forwards it to extensions instead of using its own `msg.sender`.

2. **Check `recipient` instead of `sender` in `SwapAllowlistExtension`** (partial fix, only correct when recipient == user): not generally correct for multi-hop or contract-recipient flows.

The cleanest fix is approach (1): extend the `swap` signature with an explicit `sender` address, have the pool validate it against `msg.sender` (or accept it only from trusted routers), and forward it to extensions. This mirrors how `addLiquidity` already separates `owner` (the economically relevant actor) from `msg.sender` (the payer/operator). [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   (admin allowlists the router for periphery access)
  - allowedSwapper[pool][alice]  = true   (alice is an individually approved user)
  - allowedSwapper[pool][eve]    = false  (eve is NOT approved)

Attack:
  1. eve calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=eve, ...)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES
  5. Eve's swap executes against the curated pool's liquidity

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; eve trades against LP funds deposited under the assumption
          that only vetted counterparties (alice and similar) would trade
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-162)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
  /// @param callbackData Opaque bytes forwarded unmodified to the modify-liquidity callback.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Total token0 actually pulled from the callback into the pool (native).
  /// @return amount1Added Total token1 actually pulled from the callback into the pool (native).
  /// @dev Reverts `LiquidityDeltaLengthMismatch` when `binIdxs` and `shares` lengths differ.
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (uint256 amount0Added, uint256 amount1Added);
```

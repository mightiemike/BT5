### Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Any Unprivileged Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the originating user. If the pool admin allowlists the router (the natural step to let allowlisted users access the pool via the router), every unprivileged user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` is a plain `external` function; it does not accept a "true originator" argument. The pool records `msg.sender` as the `sender` it forwards to every extension hook:

```solidity
// ExtensionCalling.sol – _beforeSwap
function _beforeSwap(
    address sender,   // ← always msg.sender of pool.swap()
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap,
            (sender, recipient, zeroForOne, amountSpecified,
             priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64,
             extensionData))
    );
}
```

`SwapAllowlistExtension.beforeSwap` performs its allowlist lookup keyed on `(pool, sender)`. When a user calls `MetricOmmSimpleRouter.exactInput` or `exactInputSingle`, the router is the direct caller of `pool.swap`, so `sender` = router:

```solidity
// MetricOmmSimpleRouter.sol – exactInput (all hops)
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        MetricOmmSwapPath.openLimit(zeroForOne),
        "",
        params.extensionDatas[i]   // extension sees sender = router
    );
```

The pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the extension's `beforeSwap` check passes for **every** caller of the router, regardless of whether that caller is on the allowlist.

The same structural issue applies to `exactOutputSingle`, `exactOutput`, and every intermediate hop of `exactInput`/`exactOutput`: in all cases `msg.sender` seen by the pool is the router, not the originating EOA.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is typically restricted to a curated set of counterparties (e.g., KYC-verified traders, institutional partners, or whitelisted arbitrageurs). Once the router is allowlisted, any unprivileged user can:

1. Call `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
2. The pool's `beforeSwap` hook sees `sender = router` → allowlist check passes.
3. The non-allowlisted user executes a swap at the oracle-anchored bid/ask price.

If the oracle price is even slightly favorable (stale, at a spread boundary, or during a volatile period), the non-allowlisted user extracts value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal and constitutes a broken access-control invariant with fund-impacting consequences.

**Severity: High** — the bypass is unconditional once the router is allowlisted; no special privilege or token is required.

---

### Likelihood Explanation

- The router is a public, permissionless periphery contract.
- Pool admins who deploy a swap-allowlisted pool and also want allowlisted users to use the router **must** add the router to the allowlist; there is no other supported path.
- Any attacker who observes `allowedSwapper[pool][router] = true` on-chain can immediately exploit the bypass.
- No admin action, flash loan, or special setup is required beyond a standard `exactInputSingle` call.

**Likelihood: High.**

---

### Recommendation

Pass the originating user's address through the extension data or add a dedicated `originator` field to the `beforeSwap` hook signature. A minimal fix is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router. A more robust fix is to add an `originator` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates from a verified transient-storage slot set by the router before calling `swap`.

---

### Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin adds Alice (allowlisted user) and the router to allowedSwapper[pool].
3. Bob (non-allowlisted) is NOT in allowedSwapper[pool].

Attack
──────
4. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient=Bob, ...).
   → msg.sender seen by pool = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
8. Bob's swap executes at oracle price; Bob is never checked against the allowlist.

Result
──────
Bob, an unprivileged non-allowlisted user, successfully swaps in a pool
that was configured to restrict access to vetted counterparties only.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

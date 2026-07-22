### Title
SwapAllowlistExtension gates the router address instead of the originating user, allowing any caller to bypass the per-pool swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the original user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), every unpermissioned address in the network can bypass the allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the `_beforeSwap` dispatcher is called with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that value verbatim as the `sender` parameter to every configured extension: [2](#0-1) 

**Step 2 — The allowlist extension checks that forwarded `sender`.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the direct caller of the pool) as the identity being gated: [3](#0-2) 

**Step 3 — The router is the direct caller of the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The original `msg.sender` of the router call is stored only in transient storage for the payment callback; it is never forwarded to the pool as the swap initiator: [4](#0-3) 

When the pool executes, `msg.sender == router`, so `sender == router` in the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

The same identity substitution occurs in `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

---

### Impact Explanation

The pool admin faces an inescapable dilemma:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router; they must call the pool directly. The router — the canonical periphery entrypoint — is broken for the pool. |
| Router **allowlisted** | Every address in the network can bypass the allowlist by routing through the router. The allowlist provides zero protection. |

In the second (operationally necessary) case, a non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool. The extension sees `sender = router`, finds `allowedSwapper[pool][router] == true`, and permits the swap. The user receives output tokens from a pool they were explicitly barred from accessing. On a pool designed for institutional or KYC-gated counterparties, this constitutes a direct loss of the protection the pool admin paid to configure, and any LP who deposited under the assumption of a closed pool is exposed to toxic or unintended flow.

---

### Likelihood Explanation

- The router is the standard, documented entrypoint for swaps. Any pool admin who wants their allowlisted users to have a normal UX must add the router to the allowlist.
- No special privilege, flash loan, or multi-step setup is required. A single `exactInputSingle` call from any EOA suffices.
- The bypass is silent: the extension emits no event distinguishing a router-mediated bypass from a legitimate direct call.

---

### Recommendation

The extension must gate the **economically responsible actor**, not the direct pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to attest the real user**: Add a router-aware path in the extension that verifies a signed or encoded originator, similar to how `DepositAllowlistExtension` correctly gates `owner` (the economic beneficiary) rather than `sender` (the direct caller): [6](#0-5) 

The deposit extension is correct because `owner` is the address that receives LP shares regardless of who calls the pool. The swap extension must adopt the same principle: identify and check the address that economically benefits from the swap, not the contract that mechanically forwards it.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice]  = true   (alice is the intended gated user)
  allowedSwapper[P][router] = true   (admin adds router so alice can use it)
  allowedSwapper[P][bob]    = false  (bob is explicitly excluded)

Attack (bob bypasses the allowlist):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, ...)
      → msg.sender inside P == router
      → _beforeSwap(sender=router, ...)
        → E.beforeSwap(sender=router, ...)
          → allowedSwapper[P][router] == true  ✓ passes
    → swap executes; bob receives output tokens from the curated pool

Result:
  bob, who is explicitly excluded from pool P, successfully swaps.
  The allowlist is completely ineffective for any router-mediated call.
```

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

Looking at the `SwapAllowlistExtension` and how the `MetricOmmSimpleRouter` calls the pool, I can trace a concrete wrong-actor binding issue that is the direct structural analog to the Revolver domain-separation bug.

---

### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the individual user. If the pool admin allowlists the router to enable router-based swaps for their curated users, any unprivileged user can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the only caller permitted by `onlyPool`). `sender` is the first argument forwarded by the pool — which is `msg.sender` of the original `pool.swap()` call, set in `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient, ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` executes, it calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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
```

The actual user's address (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the callback — it is **never passed to the pool or to any extension**. The pool sees `msg.sender = router`. The extension therefore evaluates:

```
allowedSwapper[pool][router_address]
```

not

```
allowedSwapper[pool][actual_user]
```

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A pool admin deploying a curated pool (e.g., KYC-only, institution-only) with `SwapAllowlistExtension` will naturally want to support the standard router. They allowlist the router address so their approved users can swap through it. This single admin action silently opens the pool to **every user on-chain**: any address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool, the extension checks `allowedSwapper[pool][router]` → `true`, and the swap executes without any per-user check.

The allowlist guard is completely neutralized for all router-mediated swaps. Non-allowlisted users gain full swap access to a pool that was designed to exclude them, undermining the pool's curation policy and any regulatory or risk controls it was meant to enforce.

The converse failure is equally broken: if the admin does **not** allowlist the router, legitimate allowlisted users cannot use the router at all — the extension reverts on `allowedSwapper[pool][router]` → `false` — making the standard swap path unusable for the pool's intended participants.

---

### Likelihood Explanation

This is a realistic, high-probability misconfiguration. Pool admins who configure a per-user allowlist will also want their users to benefit from the router's slippage protection, deadline checks, and multi-hop routing. Allowlisting the router is the obvious and expected step. Nothing in the extension's interface, NatDoc, or admin setter warns that doing so collapses the per-user gate. The bypass requires no special privilege, no flash loan, and no multi-block setup — any EOA can trigger it in a single transaction.

---

### Recommendation

The extension must check the **economically relevant actor**, not the immediate `msg.sender` of `pool.swap()`. Two sound approaches:

1. **Router passes actual user in `extensionData`**: Require the router to ABI-encode the originating user address as the first word of `extensionData` for allowlisted pools, and have the extension decode and check that address when `sender` is a known router.

2. **Check `sender` only when it is not a trusted forwarder**: Maintain a registry of trusted forwarder contracts; when `sender` is a forwarder, decode the real user from `extensionData`; otherwise check `sender` directly.

3. **Document the invariant clearly**: At minimum, document that allowlisting any shared contract (router, aggregator) collapses per-user gating to per-contract gating, and that pool admins must never allowlist shared intermediaries if individual-user control is required.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin allowlists alice (KYC'd) and the router R:
    E.setAllowedToSwap(P, alice, true)
    E.setAllowedToSwap(P, R, true)   ← intended to let alice use the router

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  router calls P.swap(recipient, ...) with msg.sender = R
  pool calls _beforeSwap(R, ...)
  extension checks allowedSwapper[P][R] → true
  swap executes — bob bypasses the per-user allowlist entirely

Result:
  bob swaps on a pool that was designed to exclude him.
  alice's KYC-only pool is now open to all users via the router.
```

The structural analog to the Revolver bug is exact: just as the Revolver signed payload omitted `address(this)` and `block.chainid` so a signature valid for one deployment was valid verbatim in another, `SwapAllowlistExtension` omits the actual user's identity so an allowlist entry valid for the router is valid verbatim for every user who routes through it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

### Title
`SwapAllowlistExtension` checks the router's address instead of the originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to gate swaps on a curated pool by checking whether the swapper is allowlisted. However, the hook receives `sender` as the immediate `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), every user — including those not individually allowlisted — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Hook argument binding:**

In `MetricOmmPool.swap`, the `_beforeSwap` call passes `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first positional argument to every configured extension: [2](#0-1) 

**The guard check:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

**The router path:**

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any of `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap(...)` directly. At that point `msg.sender` inside the pool is the **router address**, so `sender` forwarded to the extension is the router, not the originating user: [4](#0-3) 

**The dilemma this creates:**

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Every router-mediated swap reverts `NotAllowedToSwap`, even for individually allowlisted users — the router is unusable on the pool |
| Router **is** allowlisted | Every user, including those not individually allowlisted, can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users. The `extensionData` bytes are passed through but `SwapAllowlistExtension` ignores them entirely, so there is no in-band way to carry the originating user's identity to the extension. [3](#0-2) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural operational choice to let approved users trade via the standard periphery) inadvertently opens the pool to **all** users. Non-allowlisted actors can:

- Execute swaps on a pool explicitly designed to restrict access.
- Front-run or sandwich allowlisted LPs whose positions were sized for a controlled participant set.
- Drain LP value through repeated arbitrage that the allowlist was intended to prevent.

The allowlist guard — the only on-chain enforcement of the pool's curation policy — silently fails open for every router-mediated swap. This constitutes broken core pool functionality with direct LP-principal exposure.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps.
- Any pool admin who wants allowlisted users to trade via the router must allowlist the router, triggering the bypass.
- No special privilege, flash loan, or multi-step setup is required; a single `exactInputSingle` call from any EOA suffices.
- The bypass is silent — no event or revert signals that the per-user check was skipped.

---

### Recommendation

The extension must identify the **originating user**, not the immediate pool caller. Two viable approaches:

1. **Encode the originating user in `extensionData`**: The router encodes `msg.sender` into the `extensionData` it forwards to the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks that address. This requires a trusted encoding convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For direct swaps the recipient is often the user; however this is not reliable for multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-aware allowlist logic**: Maintain a secondary mapping `allowedSwapper[pool][router][originator]` and require the router to pass the originator in `extensionData`, verified by the extension.

The simplest safe fix is option 1, with the router always prepending `abi.encode(msg.sender)` to `extensionData` and the extension decoding and checking that value when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  allowedSwapper[pool][userA] = true          // legitimate user
  allowedSwapper[pool][router] = true         // admin adds router so userA can use it

Attack:
  userB (not in allowedSwapper) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      tokenIn: token1,
      zeroForOne: false,
      amountIn: X,
      recipient: userB,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=userB, ..., extensionData)   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, userB receives token0

Result: userB swapped successfully despite not being in the per-user allowlist.
``` [3](#0-2) [1](#0-0) [5](#0-4)

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

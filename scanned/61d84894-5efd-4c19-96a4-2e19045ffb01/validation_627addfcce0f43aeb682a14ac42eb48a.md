### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the immediate `msg.sender` of `pool.swap()` — against the per-pool allowlist. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router contract, not the end-user. If the pool admin allowlists the router (a natural step to let legitimate users access the router), every unpermissioned user can bypass individual-user gating by routing through the router.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` checks the wrong actor.** [1](#0-0) 

The hook receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded.

**The pool forwards `msg.sender` of the `swap` call as `sender`.** [2](#0-1) 

`_beforeSwap(sender, recipient, ...)` is called by the pool with `sender = msg.sender` of the `swap` call.

**The router calls `pool.swap(recipient, ...)` — making itself `msg.sender`.** [3](#0-2) 

For `exactInputSingle`, the router calls `pool.swap(params.recipient, ...)`. The pool sees `msg.sender = router`, so it passes `sender = router` to `_beforeSwap`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**Consequence — the "Shorten Lock" analog:**

| External bug | Metric OMM analog |
|---|---|
| `_to.stakeUntil` overwritten by `_from.stakeUntil` (shorter lock) | `allowedSwapper[pool][router]` (weaker gate) evaluated instead of `allowedSwapper[pool][user]` (stronger gate) |
| Destination's stronger protection is silently replaced | Individual-user restriction is silently replaced by router-level permission |
| Fix: require `_to.amount == 0` | Fix: check the actual end-user identity, not the intermediary |

The pool admin cannot simultaneously:
- Allowlist the router (so legitimate users can use it), **and**
- Block non-allowlisted users from routing through it.

Allowlisting the router opens the gate for every user.

---

### Impact Explanation

Any user not on the swap allowlist can bypass the curation policy by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) on a pool that has `SwapAllowlistExtension` active and the router address allowlisted. The allowlist — the pool's primary access-control boundary — silently fails open for all router-mediated swaps. On pools designed for restricted counterparties (e.g., institutional-only or KYC-gated pools), this allows unauthorized principals to trade, violating the pool's invariant and potentially draining LP-owned liquidity at oracle prices.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router. This is a natural and expected configuration step: without it, even legitimately allowlisted users cannot use the router. Any pool that wants to support router-based swaps for its approved users must allowlist the router, inadvertently opening the gate to all users.

---

### Recommendation

The `beforeSwap` hook must check the actual end-user identity, not the intermediary. Two viable approaches:

1. **Router passes the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to both the router and the extension.

2. **Pool exposes an `originSender` field**: The pool's `swap` function accepts an explicit `sender` parameter (the originating user) alongside `recipient`, and the pool enforces that `msg.sender` is an approved operator for that sender. The extension then checks the declared `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is allowed
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so Alice can use it

Attack:
  charlie (not on allowlist) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  router calls:
    pool.swap(charlie, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender = router

  pool calls:
    _beforeSwap(router, charlie, ...)

  SwapAllowlistExtension checks:
    allowedSwapper[pool][router]  →  true  ✓  (bypass succeeds)

  Charlie's swap executes on the restricted pool.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

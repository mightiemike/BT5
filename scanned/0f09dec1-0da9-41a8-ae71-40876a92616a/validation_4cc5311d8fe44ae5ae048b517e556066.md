### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the router — not the originating user. A pool admin who allowlists the router (required for any router-mediated swap to work) inadvertently opens the gate to every user, defeating the per-user curation the extension was designed to enforce.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces its guard as:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument forwarded by the pool through `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

The pool populates `sender` with its own `msg.sender` — the contract that called `pool.swap`. When `MetricOmmSimpleRouter` executes any swap variant, it is the direct caller of `pool.swap`:

```solidity
// MetricOmmSimpleRouter.sol L72-80  (exactInputSingle)
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

The pool therefore sees `msg.sender = router` and passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The same pattern holds for `exactInput` (all hops call `pool.swap` from the router), `exactOutputSingle`, and the recursive `exactOutput` path (intermediate hops are called from inside `metricOmmSwapCallback`, which also executes in the router's context).

**Consequence of the only two viable admin configurations:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert, even for individually allowlisted users — the router is unusable |
| Allowlist the router | Every user on the network can swap through the router regardless of their individual allowlist status — the per-user gate is nullified |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

Any user excluded from a curated pool's allowlist can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The router is a public, permissionless contract. The bypassing user receives pool output tokens they were not authorized to receive, and the pool's LP providers are exposed to trades from counterparties the pool admin explicitly intended to exclude. This is a direct, fund-impacting policy failure on every pool that deploys `SwapAllowlistExtension` and expects per-user curation.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any user who reads the protocol documentation or observes on-chain interactions will naturally use it. No special knowledge, flash loan, or privileged role is required — a single `exactInputSingle` call from any EOA is sufficient to trigger the bypass. The pool admin has no on-chain mechanism to detect or prevent this routing.

---

### Recommendation

The extension must gate on the economically relevant actor, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives output tokens and is the actor the pool admin most plausibly intends to gate. The pool already forwards `recipient` as the second argument to `beforeSwap`.

2. **Require the router to forward the originating user** — the router could encode the originating `msg.sender` into `extensionData`, and the extension could decode and check that value. This requires a coordinated convention between the router and the extension.

Option 1 is simpler and requires no router changes. Option 2 is more flexible but introduces a trust dependency on the router encoding the correct user.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)      // required for router to work
  - Admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  1. Alice (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: alice, ...})

  2. Router calls:
       pool.swap(alice /*recipient*/, zeroForOne, amount, limit, "", extensionData)
       // pool.msg.sender = router

  3. Pool calls:
       extension.beforeSwap(sender=router, recipient=alice, ...)
       // msg.sender = pool

  4. Extension evaluates:
       allowedSwapper[pool][router] == true  →  guard passes

  5. Alice receives output tokens despite never being individually allowlisted.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

### Title
`SwapAllowlistExtension` checks the router address instead of the actual swapper, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's address — not the actual user's address — against the allowlist. Any pool admin who allowlists the router to permit allowlisted users to trade through it simultaneously opens the gate to every non-allowlisted user.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`, which encodes it into the extension call: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that value verbatim as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool call: [4](#0-3) 

The same pattern applies to `exactInput` and `exactOutput`: [5](#0-4) 

**The invariant break:** The pool admin faces an impossible choice:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | Every non-allowlisted user can bypass the guard via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` (e.g., to restrict trading to KYC'd counterparties, institutional participants, or specific protocol addresses) is completely defeatable by any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user receives the same swap execution as an allowlisted user, including access to pool liquidity and oracle-priced fills. This is a direct loss of the curation guarantee the pool admin paid to enforce, and may expose LPs to counterparty risk they explicitly opted out of.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard supported periphery path. Any user can call it permissionlessly. No special role, token balance, or prior state is required. The bypass is a single `exactInputSingle` call with the target pool address.

---

### Recommendation

The extension must identify the **economic actor** — the address whose funds are at risk and whose identity the allowlist is meant to gate — not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Check `sender` only when `sender != router`; require the router to attest the real user**: The extension is made router-aware and validates an authenticated user claim from the router's `extensionData`.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly supplied by the caller), not `sender` (the immediate pool caller): [6](#0-5) 

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`; checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

If the admin does **not** add the router in step 3, Alice also cannot use the router — confirming the impossible dilemma.

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

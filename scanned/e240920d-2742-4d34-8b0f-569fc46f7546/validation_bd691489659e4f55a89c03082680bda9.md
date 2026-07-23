### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the extension sees the router's address — not the actual user. If the pool admin allowlists the router to support router-mediated swaps, every unprivileged user can bypass the curated allowlist by routing through it.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput` or `exactOutput`, the router calls `pool.swap(...)` on the user's behalf: [4](#0-3) 

At that point `msg.sender` of the pool call is the **router contract**, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

This produces two mutually exclusive failure modes:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot swap through the router at all — legitimate UX is broken |
| Router **allowlisted** | **Every** user can bypass the curated allowlist by routing through the router |

The second mode is the critical one. A pool admin who wants to restrict swaps to a known set of addresses and also wants those addresses to be able to use the standard router must allowlist the router. Doing so silently opens the pool to all users, defeating the entire curation purpose.

The `DepositAllowlistExtension` does **not** share this flaw: it gates on `owner` (the position owner explicitly passed by the caller), not on `sender` (the payer/operator), so the operator pattern does not create an analogous bypass there: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter`. The user receives pool output tokens and the pool's LP positions absorb the trade as if the allowlist did not exist. This is a direct loss of the curation guarantee and, depending on pool design, can result in LP value leakage to disallowed counterparties.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the periphery. Any user who discovers that direct `pool.swap()` is blocked but router-mediated swaps succeed (because the router is allowlisted) can immediately exploit this. No privileged access, no special token, and no multi-step setup is required — a single `exactInput` call suffices.

---

### Recommendation

The extension must resolve the **actual user** rather than the direct pool caller. Two sound approaches:

1. **Pass the user through `extensionData`**: The router encodes `msg.sender` (the user) into `extensionData`; the extension decodes and gates on that value. This requires the extension to trust the encoding, which is fragile.

2. **Gate on `recipient` instead of `sender`** (if the pool's intent is to restrict who receives output): swap the checked field to `recipient`, which the user controls and which the router forwards unchanged.

3. **Preferred — resolve via callback context**: Redesign the hook signature to carry a dedicated `originator` field that the pool populates from a trusted source (e.g., a transient-storage slot written before the extension call), so the extension always sees the economic actor regardless of intermediary.

At minimum, the `SwapAllowlistExtension` NatSpec and the pool documentation must warn that allowlisting the router opens the pool to all users, and that direct-pool-only deployments are the only safe configuration today.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router UX
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
     targeting the curated pool.
  2. Router calls pool.swap(recipient=attacker, ...) → msg.sender of pool call = router.
  3. _beforeSwap passes sender=router to SwapAllowlistExtension.beforeSwap.
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. Swap executes; attacker receives output tokens.
  6. Allowlist is fully bypassed with zero privileged access.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L151-188)
```text
  ///      recursively inside `metricOmmSwapCallback`: each callback pays the current hop's input, then (unless on
  ///      the last pool) swaps the next pool for exactly that input amount. The first swap's input delta is total
  ///      `amountIn`.
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
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

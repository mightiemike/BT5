### Title
`SwapAllowlistExtension` Checks Immediate Pool Caller (`sender`) Instead of Economic Actor, Allowing Any User to Bypass Swap Allowlist via Shared Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through the shared `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. A pool admin who allowlists the router (to let their permitted users trade via the router) inadvertently opens the gate to every user on the router, completely defeating the curation policy.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

**What `sender` actually is:**

In `MetricOmmPool.swap()`, `sender` is always `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

**What the router passes as `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making the router the `msg.sender` of the pool's `swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The wrong-actor binding:**

| Entry path | `sender` seen by extension | Allowlist check |
|---|---|---|
| User calls pool directly | User's EOA | Correct — gates the user |
| User calls via `MetricOmmSimpleRouter` | Router contract address | Wrong — gates the router, not the user |

A pool admin who wants their allowlisted users to be able to use the router must add the router address to the allowlist. But `MetricOmmSimpleRouter` is a **shared, permissionless contract** — any user can call it. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check at line 37 passes for every user who routes through it, regardless of whether that user is individually permitted.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates the `owner` parameter (the position beneficiary), not `sender` (the intermediary caller). The `MetricOmmPoolLiquidityAdder` always passes the actual user's address as `owner`, so the deposit allowlist is not affected by this class of bypass. [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, KYC'd addresses, or protocol-owned accounts) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The disallowed user receives pool output tokens and the pool receives input tokens at oracle-derived prices — a direct, real-value trade that the pool admin explicitly intended to block. This breaks the core pool functionality the allowlist was configured to enforce and constitutes a direct policy bypass with fund-impacting consequences (the pool trades with counterparties it was configured to reject).

---

### Likelihood Explanation

The trigger is a natural operational mistake. A pool admin who configures `SwapAllowlistExtension` and also wants their permitted users to access the router will add the router to the allowlist — this is the only way to make router-based swaps work for allowed users. The moment the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, no malicious setup, and no non-standard tokens. The `MetricOmmSimpleRouter` is a public, documented periphery contract.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original initiator through the router.** Add an optional `payer`/`originator` field to the router's swap path that is forwarded as `extensionData`. The extension can decode it and verify it against the allowlist. The pool admin then allowlists individual users, not the router.

2. **Alternatively, check `sender` only when it is not a known router.** The extension could maintain a registry of trusted routers and, when `sender` is a trusted router, require the actual user address to be encoded in `extensionData` and verified there.

The `DepositAllowlistExtension` pattern (gating `owner`, which is always the position beneficiary regardless of intermediary) is the correct model to follow for the swap case.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin allowlists alice (allowed user) and router (so alice can use the router)
  - allowedSwapper[pool][alice]  = true
  - allowedSwapper[pool][router] = true
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...) → msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...):
       allowedSwapper[pool][router] == true  → check passes
  5. Swap executes; bob receives output tokens
  6. Pool admin's curation policy is defeated
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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

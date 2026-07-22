### Title
`SwapAllowlistExtension` gates the router's address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. A pool admin who allowlists the router address to enable router-mediated swaps inadvertently opens the gate to every user on the internet, completely defeating the per-user curation the extension is meant to enforce.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the only caller that passes `onlyPool`) and `sender` is the value the pool forwarded.

**What the pool forwards as `sender`**

`MetricOmmPool.swap` passes `msg.sender` — the direct caller of the pool — as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end-user
    recipient,
    ...
);
``` [2](#0-1) 

**How the router calls the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The router is `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
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

**The broken invariant**

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Two failure modes follow:

1. **Allowlist bypass (high impact):** The pool admin allowlists the router address so that their approved users can swap via the router. Because the check is keyed on the router, every user on the internet — including those the admin explicitly never approved — can now swap by calling `router.exactInputSingle(...)`. The per-user curation is completely voided.

2. **Broken core functionality (medium impact):** The pool admin allowlists individual user addresses (the intended design). Those users cannot swap through the router because the router is not on the allowlist. They are forced to call `pool.swap()` directly, which requires them to implement `IMetricOmmSwapCallback` themselves — a capability ordinary EOAs do not have. The pool is effectively unusable for its intended audience.

Neither failure mode requires any privileged action beyond the admin's natural configuration of the extension.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. An unauthorized user can execute swaps, drain LP-owned liquidity at oracle-derived prices, and extract value that the pool's access policy was designed to prevent. This is a direct loss of LP principal and a broken core pool invariant (swap conservation under access control).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed by the protocol. Any pool admin who wants their allowlisted users to be able to use the standard router will allowlist the router address — this is the only way to make router-mediated swaps work. The bypass is therefore reachable on any production pool that combines `SwapAllowlistExtension` with router support, which is the expected deployment pattern.

---

### Recommendation

The extension must gate the **economic actor** (the end-user), not the intermediary. Two approaches:

1. **Forward the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the router's encoding, which introduces its own trust assumptions.

2. **Check `sender` only for direct pool calls; require a signed or verifiable identity for router calls:** The extension can detect whether `sender` is a known router and, if so, require the actual user identity to be supplied and verified in `extensionData`.

3. **Preferred — gate on `sender` but document that the router must not be allowlisted:** Add an explicit guard in `setAllowedToSwap` that rejects known router addresses, and document that per-user allowlisting is only enforceable for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Admin calls setAllowedToSwap(pool, router, true)
    (natural config: "let my users swap via the router").
  - Admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
  2. Router calls pool.swap(attacker, ...) — msg.sender of pool = router.
  3. Pool calls extension.beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. Swap executes. Attacker receives output tokens.

Result:
  - Attacker swapped on a pool they were never authorized to access.
  - LP funds transferred to attacker at oracle price.
  - SwapAllowlistExtension provided zero protection.
``` [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

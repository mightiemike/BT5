### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass the curated-pool allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool boundary, so the allowlist evaluates the **router's address** rather than the **real user's address**. If the router is allowlisted (or `allowAllSwappers` is set), every user on the planet can trade on a curated pool regardless of their individual allowlist status.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to every extension hook:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool boundary:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

The result is a structural actor-binding mismatch: the allowlist is keyed on `(pool → swapper)`, but the value actually evaluated is `(pool → router)`.

---

### Impact Explanation

Two failure modes, both fund-impacting:

1. **Allowlist bypass (High):** The pool admin allowlists the router address (a natural operational choice so that normal users can trade). Every non-allowlisted address can now trade on the curated pool simply by going through the router. The entire curation policy is silently voided. Disallowed counterparties can drain liquidity or execute trades the pool admin explicitly intended to block.

2. **Allowlist over-block:** If the router is *not* allowlisted, every allowlisted user who routes through the router is blocked, breaking core swap functionality for legitimate participants.

Both outcomes are direct consequences of the guard checking the wrong actor.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary documented user-facing swap entrypoint.
- Pool admins who configure a `SwapAllowlistExtension` will almost certainly also allowlist the router (or set `allowAllSwappers = true` for the router) so that normal trading works — which is precisely the condition that opens the bypass.
- No privileged access or special setup is required; any EOA can call `exactInputSingle`.

---

### Recommendation

The extension must evaluate the **real initiating user**, not the intermediary. Two complementary fixes:

1. **Check `sender` from the router's stored callback context.** The router already records the real payer via `_setNextCallbackContext(..., msg.sender, ...)`. The extension could read this from transient storage, or the pool could forward it as a dedicated field.

2. **Alternatively, gate on `recipient` or require the pool to pass the original initiator** as a separate, unforgeable parameter distinct from the call-chain `msg.sender`.

3. **Short-term mitigation:** Document that `SwapAllowlistExtension` is incompatible with any intermediary router and must only be used with direct pool calls until the actor binding is corrected.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (or setAllowAllSwappers(pool, true) to allow router-based trading)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; attacker receives output tokens

Result:
  attacker, who is NOT on the allowlist, successfully swaps on a curated pool.
  The allowlist guard is completely bypassed through the supported router path.
```

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the position owner explicitly passed by the caller), not `sender`, so the real depositor identity is preserved even through a liquidity-adder intermediary. [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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

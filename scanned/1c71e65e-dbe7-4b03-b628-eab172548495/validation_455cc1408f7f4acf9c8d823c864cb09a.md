### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any caller to bypass a pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If the pool admin allowlists the router (the only way to let legitimate users use the router), every user on-chain can bypass the allowlist by routing through it.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to the extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

At that point `msg.sender` inside `pool.swap()` is the **router**, so `sender` delivered to the extension is the router address, not the originating user. The extension therefore checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The same actor-substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`, and for the recursive callback hops in `_exactOutputIterateCallback`: [4](#0-3) 

The pool admin faces an impossible choice:

- **Allowlist the router** → every user on-chain can bypass the allowlist by routing through it.
- **Do not allowlist the router** → every legitimately allowlisted user is broken on the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) is fully open to any caller who routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool's liquidity, and the pool receives real token input, so there is no revert — the swap settles normally. The allowlist protection is silently voided. This is a **High** severity allowlist bypass with direct fund-flow consequences: unauthorized users extract value from a pool whose admin explicitly intended to restrict access.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint documented and expected to be used by all users. Any pool admin who deploys a swap-allowlisted pool and wants legitimate users to use the router must allowlist the router address, which immediately opens the bypass to everyone. The trigger requires no special privilege, no flash loan, and no unusual token — just a standard `exactInputSingle` call. Likelihood is **High**.

---

### Recommendation

The extension must gate the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router.** `MetricOmmSimpleRouter` already tracks `msg.sender` in transient storage as the payer. The router should encode the originating user in `extensionData` and the extension should decode and check it — or the pool interface should carry a separate `originator` field.

2. **Alternatively, check `recipient` or a signed identity.** If the pool's design intent is to gate who *receives* output, check `recipient` instead of `sender`. If the intent is to gate who *initiates* the trade, the originating address must be threaded through the router explicitly.

Until fixed, pool admins should not rely on `SwapAllowlistExtension` for any pool reachable through `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that legitimate users can use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       tokenIn: token0,
       recipient: attacker,
       amountIn: X,
       ...
     })
  2. Router calls pool.swap(attacker, zeroForOne, X, ...) with msg.sender = router.
  3. Pool calls extension.beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. Swap executes; attacker receives token1 output.

Result:
  attacker, who is not on the allowlist, successfully swaps in a
  pool that was intended to be restricted.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

### Title
`SwapAllowlistExtension` checks the router's address as the swapper identity, allowing any user to bypass the per-pool swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the address the pool passes as the caller. `MetricOmmPool.swap` always passes its own `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the natural configuration for a pool that wants to support router-mediated swaps), every user — including those the allowlist is meant to exclude — can bypass the gate by calling through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

In `MetricOmmPool.swap`, the `_beforeSwap` call is:

```solidity
_beforeSwap(
  msg.sender,   // ← always the direct caller of swap()
  recipient,
  ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` argument to every configured extension:

```solidity
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` gates on that `sender`.**

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The check is `allowedSwapper[pool][sender]`. When a user calls the pool directly, `sender` is the user — correct. When a user calls through the router, `sender` is the router contract.

**Step 3 — The router calls the pool as `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

The pool receives `msg.sender = address(router)`. It passes `address(router)` as `sender` to `_beforeSwap`. The extension then evaluates `allowedSwapper[pool][router]`.

**Step 4 — The bypass.**

A pool admin who wants to support router-mediated swaps for allowlisted users will call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router — regardless of who the actual end user is. Any address, including those the admin explicitly excluded, can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and the allowlist is silently bypassed.

The same bypass applies to the multi-hop `exactInput` path, where intermediate hops also call `pool.swap` with `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade in a pool. Bypassing it lets any unpermissioned address execute swaps against a pool that is supposed to be restricted (e.g., KYC-gated, institutional-only, or regulatory-compliant pools). The attacker receives real token output from the pool in exchange for token input — a direct, fund-impacting consequence. The pool's LP providers are exposed to trades from counterparties the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is the natural and expected configuration for any allowlist-protected pool that also wants to support the standard periphery router. The admin has no way to simultaneously (a) allow router-mediated swaps for permitted users and (b) block router-mediated swaps for unpermitted users, because the extension receives only the router's address. The misconfiguration is therefore not a mistake — it is the only available configuration for router-compatible allowlisted pools.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **end user**, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward `msg.sender` (the actual user) as an additional field in `extensionData` or as the `recipient`-equivalent argument, and the extension should read it from there.

2. **Alternatively, gate on `recipient` instead of `sender` for router flows**, or introduce a dedicated `originalSender` field in the extension interface so the pool can carry the true initiator through the call chain.

The `DepositAllowlistExtension` does not share this bug because it gates on `owner` (the position owner explicitly supplied by the caller), not on `sender` (the intermediary contract). [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Admin calls setAllowedToSwap(pool, alice, true)    // alice is the only permitted user
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — pool sees msg.sender = router.
  3. Pool calls _beforeSwap(router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. bob receives token output.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; bob bypasses the allowlist.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

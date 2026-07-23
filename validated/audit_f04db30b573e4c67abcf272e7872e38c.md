### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the extension checks whether the **router** is allowlisted, not the actual end user. A pool admin who allowlists the router to support router-based swaps inadvertently opens the gate to every user on-chain, completely defeating the allowlist.

---

### Finding Description

In `MetricOmmPool.swap`, the pool captures `msg.sender` and forwards it as `sender` to every before-swap extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses `msg.sender` (the pool) as the mapping key and `sender` (the immediate caller of `pool.swap`) as the identity being gated: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so `sender` = router address. The allowlist lookup becomes `allowedSwapper[pool][router]`. Two outcomes follow:

1. **Router not allowlisted** — every router-mediated swap reverts with `NotAllowedToSwap`, breaking the standard swap UX for all users on the curated pool.
2. **Router allowlisted** (pool admin adds it to unblock router UX) — `allowedSwapper[pool][router]` is `true`, so the check passes for **any** caller who routes through the router, regardless of whether that caller is on the per-user allowlist.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates the `owner` parameter (the economically relevant actor), not `sender` (the immediate caller): [4](#0-3) 

The asymmetry confirms the swap extension checks the wrong identity.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd or permissioned addresses is fully bypassed. Any unpermissioned user routes through `MetricOmmSimpleRouter` and trades freely, draining LP value or executing swaps the pool admin explicitly prohibited. Because the pool's oracle-priced bins hold real token balances, unauthorized swaps directly extract LP principal.

---

### Likelihood Explanation

The bypass requires the pool admin to add the router to the allowlist. This is a natural operational step: the router is the standard user-facing entry point, and a pool admin who wants to support normal UX will allowlist it without realizing it opens the gate to all users. The `generate_scanned_questions.py` audit pivot explicitly flags this path: [5](#0-4) 

---

### Recommendation

The extension must gate the actual end user, not the immediate caller. Options:

- **Pass original user through `extensionData`**: The router encodes `tx.origin` or the original caller into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
- **Align with the deposit pattern**: Introduce a `swapper` identity field (analogous to `owner` in `addLiquidity`) that the pool or router populates with the true end user, and have the extension check that field.
- **Document incompatibility**: At minimum, clearly document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that allowlisting the router voids the allowlist guarantee.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Unpermissioned user `alice` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Alice successfully swaps on a pool she was never authorized to access, bypassing the curated allowlist entirely.

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

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

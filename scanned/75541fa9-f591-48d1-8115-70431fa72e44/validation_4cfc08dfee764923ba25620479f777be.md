### Title
Swap Allowlist Checks Router Address Instead of Original User, Enabling Full Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the original user. If a pool admin allowlists the router address (a natural mistake when trying to enable router-based swaps for allowlisted users), every unprivileged user can bypass the per-pool swap allowlist by calling the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
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

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:149-177
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
   packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` = pool address, `sender` = router address. The guard evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol:71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The original user's address is stored only in transient callback context for payment settlement; it is never surfaced to the pool or the extension.

**Bypass path:** A pool admin who wants allowlisted users to be able to swap via the router calls `setAllowedToSwap(pool, router, true)`. From that point, any unprivileged user calls `router.exactInputSingle(pool, ...)`. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. The original user's allowlist status is never consulted.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap(...)` with `msg.sender = router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a private institutional pool, a KYC-gated pool, or a pool with preferential pricing for specific counterparties) is fully open to any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against LP positions, extracting value at oracle-derived prices that the pool admin intended to reserve for specific counterparties. This constitutes a broken core pool access-control invariant with direct LP fund-impact potential.

---

### Likelihood Explanation

The scenario is reachable through a natural and documented admin action. The pool admin must allowlist the router to enable router-based swaps for their allowlisted users — there is no other mechanism to do so. The admin has no way to express "allow these users via the router" without allowlisting the router address itself, which silently opens the pool to all users. The `generate_scanned_questions.py` audit target explicitly flags this exact path: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*

---

### Recommendation

Pass the original user's address through the router to the extension. One approach: encode the original `msg.sender` into `extensionData` inside the router and have the extension decode and verify it. A cleaner approach: add a `swapper` field to the router's transient context and expose it via a callback or a dedicated interface that the extension can query from the pool's `inSwap()` context. Alternatively, document explicitly that allowlisting the router grants unrestricted access and provide a separate per-user router allowlist path.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Non-allowlisted attacker calls `router.exactInputSingle({pool: pool, ...})`.
4. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap executes.
5. Attacker successfully swaps on a pool they are not individually allowlisted for.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

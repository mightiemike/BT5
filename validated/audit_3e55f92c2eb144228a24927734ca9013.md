### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps by swapper identity. However, the identity it checks is the direct caller of `pool.swap()`, not the end user. When swaps are routed through `MetricOmmSimpleRouter`, the checked identity is the router's address. If the pool admin allowlists the router (which is required for any user to swap through it), every user — including non-allowlisted ones — can bypass the restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its gate check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool. The pool's `swap()` function passes `msg.sender` — the direct caller of `pool.swap()` — as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` faithfully forwards this value:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`.

For any allowlisted user to swap through the router, the pool admin must allowlist the router address. The moment the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router — including addresses the admin never intended to allow.

The analog to the external report is exact: just as `prepareCallbackValues` feeds `newCollateralAmount`/`newLeverage` (post-update values) into a guard that should use the current (pre-update) values, `SwapAllowlistExtension` feeds the router address (the wrong identity) into a guard that should use the actual user's address. In both cases the guard receives a value that is structurally correct in type but semantically wrong, causing the guard to pass when it should block.

---

### Impact Explanation

Any unprivileged user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. A pool configured as a private trading venue (e.g., restricted to institutional counterparties, or to prevent front-running by retail) loses that restriction entirely once the router is allowlisted. Unauthorized swaps execute at oracle-derived prices, draining pool liquidity faster than the LP risk model anticipates and exposing LPs to counterparties they explicitly excluded. This breaks the admin-configured security boundary and constitutes a medium-severity admin-boundary break with direct LP-asset impact.

---

### Likelihood Explanation

Medium. The pool admin must allowlist the router for the bypass to be reachable. However, allowlisting the router is the natural and expected action whenever the admin wants allowlisted users to be able to use the standard periphery router — making this a routine misconfiguration rather than an exotic one. The `generate_scanned_questions.py` audit scaffold explicitly flags this path as a validation target, confirming it is a realistic operational scenario. [4](#0-3) 

---

### Recommendation

The extension must verify the **end user**, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it when `sender` is a known router.
2. **Check both `sender` and a user field from `extensionData`**: The extension falls back to `extensionData`-supplied identity when `sender` is a registered router, preserving backward compatibility for direct swaps.

The `DepositAllowlistExtension` should be audited for the symmetric issue on the `MetricOmmPoolLiquidityAdder` path.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so `alice` can use the router.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The router calls `pool.swap(recipient=bob, ...)` — inside the pool `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → **true** → no revert.
8. `bob`'s swap executes successfully despite never being allowlisted. [5](#0-4) [6](#0-5)

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

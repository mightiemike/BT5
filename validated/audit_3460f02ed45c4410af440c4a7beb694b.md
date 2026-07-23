### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Non-Allowlisted Users to Bypass the Swap Guard via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted — not the actual user. Any non-allowlisted user can bypass a curated pool's swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The pool's `msg.sender` is the **router**, so `sender = router address` reaches the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The same router-mediated path applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified users). Two fund-impacting outcomes follow:

1. **Allowlist bypass (High):** If the admin allowlists the router address — a natural choice to enable router-based swaps for their users — then *any* address, including non-allowlisted attackers, can call `router.exactInputSingle(pool, ...)` and trade on the curated pool. The extension sees `sender = router` (allowlisted) and passes. The attacker executes swaps that the pool's curation policy was designed to block.

2. **Broken allowlist for legitimate users (High):** If the admin does not allowlist the router, then allowlisted users cannot use the supported periphery path at all. The allowlist is effectively broken for the standard user-facing entry point.

In scenario 1, unauthorized swaps drain pool liquidity at oracle-derived prices, directly impacting LP principal and pool curation integrity.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary supported user-facing entry point for swaps.
- The router is a public, permissionless contract — any address can call it.
- The admin has no on-chain mechanism to distinguish "router called by allowlisted user" from "router called by attacker."
- The bypass requires only a standard `exactInputSingle` call with a valid pool address — no special privileges, no flash loans, no complex setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor**, not the intermediary. Two approaches:

1. **Pass the original user through the router:** Modify `MetricOmmSimpleRouter` to encode the original `msg.sender` in `extensionData`, and modify `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router. This requires a trusted router registry.

2. **Check `recipient` or a user-supplied identity field:** Redesign the extension interface to carry an explicit `originator` field that the pool populates from a trusted source (e.g., transient storage set by the router before calling the pool), so the extension always sees the real user regardless of intermediary.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-based swaps for their users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: curatedPool,
          tokenIn: token0,
          ...
          extensionData: ""
      }))

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, zeroForOne, amount, limit, "", "")
        msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  ✓ (passes)
      → swap executes, attacker receives output tokens

Result:
  Attacker bypasses the allowlist and trades on the curated pool.
  The extension never checked allowedSwapper[pool][attacker].
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

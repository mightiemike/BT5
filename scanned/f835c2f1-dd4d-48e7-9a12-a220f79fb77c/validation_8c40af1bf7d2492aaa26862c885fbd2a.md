### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` = router address. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the network can bypass the individual allowlist by routing through the router.

---

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to every `beforeSwap` hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [3](#0-2) 

Inside the pool, `msg.sender` is the router, so `sender` forwarded to the extension is the router address — not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Legitimate allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can bypass the individual allowlist by routing through the router |

The second branch is the exploitable path: once the router is allowlisted (the only way to support normal router usage), the allowlist guard is completely defeated for all users.

The project's own audit-target document explicitly identifies this as the critical invariant to verify: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the router is allowlisted, any unprivileged user can execute swaps in that pool by calling the router, receiving the full output of the swap. This is a direct loss of the access-control invariant the pool admin paid to enforce, and it exposes LP funds to trades from actors the pool was explicitly designed to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract that ordinary users are expected to use. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no admin cooperation. The router is a fixed, known address, so the bypass is permanently available once the router is allowlisted.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and checks that address instead of `sender`. This requires a coordinated encoding convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the actual beneficiary, the extension can check `recipient`. However, this is semantically different and may not hold for all router configurations (e.g., multi-hop where intermediate recipients are the router itself).

The cleanest fix is option 1, with the router always prepending `abi.encode(msg.sender)` to `extensionData` when calling allowlisted pools, and the extension decoding it as the authoritative identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router use
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  - Alice (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
  - Router calls pool.swap(recipient=alice, ...)
  - Pool passes msg.sender (= router) as `sender` to beforeSwap
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes; Alice receives output tokens

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; allowlist is bypassed
``` [5](#0-4) [1](#0-0) [3](#0-2)

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

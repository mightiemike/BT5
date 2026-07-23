### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router address to enable router-mediated swaps for their approved users, every unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension caller), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is the pool's own `msg.sender` at the time `swap()` was called:

```solidity
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, i.e. whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The pool's `msg.sender` is therefore the **router address**, not the actual end user. The extension receives `sender = router` and checks `allowedSwapper[pool][router]`. The extension ignores `extensionData` entirely, so there is no mechanism to recover the real user identity.

This creates a binary trap for pool admins:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every unprivileged user bypasses the allowlist via the router |

There is no configuration that simultaneously permits router-mediated swaps for approved users while blocking unapproved users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` (e.g., for regulatory compliance, institutional-only access, or protocol-controlled liquidity) can have its access control completely bypassed. Any user who calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` against such a pool — provided the router address is allowlisted — can execute swaps that the allowlist was designed to prevent. This breaks the core pool access invariant and allows unauthorized parties to drain or manipulate restricted liquidity.

---

### Likelihood Explanation

The scenario requires the pool admin to have allowlisted the router address. This is a natural and expected operational step: a pool admin who wants their approved users to benefit from the router's slippage protection, deadline checks, and multi-hop routing would allowlist the router. The `generate_scanned_questions.py` audit target explicitly flags this path: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

The bypass is reachable by any unprivileged user with no special setup beyond calling the public router.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the intermediary caller. Two viable approaches:

1. **Decode user from `extensionData`**: Have the router encode `msg.sender` (the real user) into `extensionData` for each hop, and have `SwapAllowlistExtension` decode and verify that address instead of `sender`.

2. **Check `sender` only for direct pool calls; reject router-mediated calls unless the real user is recoverable**: Add a convention (e.g., a standard prefix in `extensionData`) that extensions can use to identify the true originator when an intermediary is involved.

The `DepositAllowlistExtension` correctly gates on `owner` (the position recipient) rather than `sender` (the payer), which is the right pattern for liquidity. The swap allowlist needs an equivalent identity-resolution step. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     to enable router-mediated swaps for approved users.
  3. Pool admin does NOT call setAllowedToSwap(pool, alice, true)
     (alice is not an approved direct swapper).

Attack:
  4. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(...) — pool's msg.sender = router.
  6. Pool calls _beforeSwap(router, ...).
  7. Extension checks allowedSwapper[pool][router] == true → passes.
  8. Alice's swap executes successfully despite not being on the allowlist.

Result:
  Alice, an unprivileged user, swaps in a pool that was supposed to
  restrict access to approved counterparties only.
```

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

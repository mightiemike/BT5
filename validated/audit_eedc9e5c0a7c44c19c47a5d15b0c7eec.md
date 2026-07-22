### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool level is the router contract, not the actual user. `SwapAllowlistExtension.beforeSwap` gates access using `allowedSwapper[pool][sender]`, so it checks the router's allowlist status rather than the real trader's. This creates an irreconcilable two-path failure: if the router is allowlisted (required for any router user to trade), every non-allowlisted user bypasses the guard; if the router is not allowlisted, every allowlisted user is blocked from using the router.

---

### Finding Description

`MetricOmmPool.swap()` invokes `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` field of the `IMetricOmmExtensions.beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[pool][sender]` — keyed on that same `sender` value — to decide whether the swap is permitted. [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the router contract address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The `DepositAllowlistExtension` does not share this flaw: `addLiquidity` passes the user-supplied `owner` (the LP-share recipient) to `_beforeAddLiquidity`, so the deposit guard keys on the economically relevant actor. [5](#0-4) 

---

### Impact Explanation

**Bypass path (High — direct loss of curation policy):** The pool admin must allowlist the router address for any router-mediated swap to succeed. Once `allowedSwapper[pool][router] = true`, every non-allowlisted user can bypass the restriction by routing through `MetricOmmSimpleRouter`. The allowlist on a curated pool is completely ineffective for router-mediated swaps, allowing unrestricted access to pools that were designed to be gated (e.g., KYC pools, institutional-only pools, or pools with restricted counterparties).

**DoS path (High — broken core functionality):** If the pool admin does not allowlist the router, every allowlisted user who routes through the router is blocked. The router is the primary user-facing entry point; blocking it makes the pool practically unusable for all router-mediated flows, including multi-hop paths.

Both outcomes satisfy the contest-relevant impact gate: broken core pool functionality causing loss of funds or unusable swap flows, and admin-boundary break where an unprivileged path bypasses a configured guard.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects the router to be a valid entry point is immediately affected. The router is the canonical periphery swap path; pool admins who configure an allowlist will naturally allowlist the router to allow their permitted users to trade, unknowingly opening the bypass to all users. No special privileges, flash loans, or unusual token behavior are required — a standard `exactInputSingle` call from any EOA is sufficient.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the economically relevant actor — the end user — not the intermediary contract. Two complementary fixes:

1. **Pass the original caller through the extension payload.** The router should encode `msg.sender` (the actual user) into `extensionData` and the extension should decode and check that value instead of `sender`.

2. **Alternatively, check `recipient` instead of `sender` for swap allowlisting**, since `recipient` is the address that receives output tokens and is set by the actual user, not the router. This is already the pattern used by `DepositAllowlistExtension` (checking `owner`, the LP-share recipient).

The core pool's `_beforeSwap` signature already carries both `sender` and `recipient`; the extension just needs to key on the right field or on a user-supplied identity embedded in `extensionData`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook. `allowAll[pool] = false`.
2. Admin allowlists `allowedSwapper[pool][router] = true` so that permitted users can trade via the router.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is the router.
5. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → swap proceeds.
7. Attacker successfully swaps on a pool they were explicitly excluded from, bypassing the curation policy entirely.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

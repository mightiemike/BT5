### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented as gating `swap` by swapper address, per pool. However, the `sender` argument it receives is `msg.sender` of the pool's `swap()` call — which, when users route through `MetricOmmSimpleRouter`, is the router contract, not the actual user. If the pool admin allowlists the router to enable UI-based swaps, every unprivileged user can bypass the per-user allowlist and trade against the pool.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`_beforeSwap` encodes that value as the `sender` argument forwarded to every configured extension: [2](#0-1) 

**Step 2 — Router calls `pool.swap()` directly, so `msg.sender` is the router.**

`exactInputSingle` calls `pool.swap()` without forwarding the original caller: [3](#0-2) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` — in every case the pool's `msg.sender` is the router, not the end user.

**Step 3 — `SwapAllowlistExtension` checks the router address, not the user.** [4](#0-3) 

`msg.sender` inside the extension is the pool (correct key), but `sender` is the router address. The check `allowedSwapper[pool][sender]` therefore evaluates whether the **router** is allowlisted, not whether the actual user is allowlisted.

---

### Impact Explanation

Two fund-impacting scenarios arise from this misbinding:

**Scenario A — Router allowlisted to enable UI access (allowlist fully bypassed):**  
The pool admin allowlists the router so that legitimate users can trade through the standard UI. Because the check resolves to `allowedSwapper[pool][router] == true`, every unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool. The per-user allowlist is completely inoperative. Unauthorized traders can extract LP value through trades the pool admin never intended to permit — direct loss of LP principal.

**Scenario B — Router not allowlisted (allowlisted users cannot use the router):**  
The pool admin allowlists specific user addresses but not the router. Those users' swaps through the router revert (`NotAllowedToSwap`) because the extension sees the router, which is not allowlisted. The only path for allowlisted users is to call `pool.swap()` directly, which requires implementing the `IMetricOmmSwapCallback` interface. The standard periphery entry point is broken for the intended user set.

Scenario A is the contest-relevant impact: unauthorized swaps against a permissioned pool, causing LP asset loss.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the production UI entry point. Any pool that wants users to trade through the UI must allowlist the router, triggering Scenario A.
- No special privilege is required. Any address can call `MetricOmmSimpleRouter.exactInputSingle`.
- The misbinding is structural and present in every router swap path (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

---

### Recommendation

The extension must gate on the **original user**, not the immediate pool caller. Two approaches:

1. **Pass the original caller through the router.** The router stores `msg.sender` in transient storage (already done for the payer context). Add a parallel transient slot for the "originating swapper" and have the pool read it from the router — or pass it as part of `extensionData` in a standardized envelope that the extension unpacks.

2. **Check `recipient` instead of `sender` for the allowlist.** If the pool's design guarantees that the recipient is always the economic beneficiary, gating on `recipient` avoids the router-indirection problem. However, this changes the semantic of the allowlist.

3. **Document that the allowlist gates the immediate caller only**, and require pool admins to allowlist the router rather than individual users when router access is desired — but then provide a separate mechanism (e.g., a per-user signature checked inside `extensionData`) for true per-user gating through the router.

---

### Proof of Concept

```
Pool configured with SwapAllowlistExtension.
Admin allowlists router: allowedSwapper[pool][router] = true.
Admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false.

attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  → router calls pool.swap(recipient, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → allowedSwapper[pool][router] == true → passes
  → attacker's swap executes against the permissioned pool.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; attacker extracts LP value.
``` [5](#0-4) [6](#0-5) [1](#0-0)

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

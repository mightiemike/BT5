### Title
`SwapAllowlistExtension` gates on the immediate pool caller (`msg.sender`) rather than the originating user, allowing any user to bypass the per-pool swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument forwarded by the pool — which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. The allowlist therefore gates the router address, not the actual economic actor. A pool admin who allowlists the router to enable router-based swaps for curated users inadvertently opens the allowlist to every user on-chain.

---

### Finding Description

**Root cause — wrong actor binding in `beforeSwap`:**

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to the extension dispatcher: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct pool key) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router path — sender is the router, not the user:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The original user (`msg.sender` of `exactInputSingle`) is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The admin is forced into an impossible choice:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; allowlist is enforced only for direct pool calls |
| **Allowlist the router** | Every user on-chain can bypass the allowlist by routing through the router |

Neither option correctly implements "allow specific users to swap regardless of entry point." The admin cannot express per-user curation that survives the router path.

**Contrast with `DepositAllowlistExtension`:**

The deposit allowlist correctly gates on `owner` (the position owner parameter), not `sender` (the liquidity adder). The pool passes `owner` as a separate argument, so the liquidity adder path does not corrupt the identity check: [5](#0-4) 

The swap allowlist has no equivalent `owner`/`sender` separation — there is only `sender`, which collapses to the router address on the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional market makers, or whitelisted bots) loses that protection the moment the pool admin allowlists the router to support standard periphery usage. Any unprivileged user can then call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity without being in the allowlist. LP funds are exposed to unauthorized counterparties, violating the curation invariant the extension was deployed to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard public swap entry point for the protocol. A pool admin who deploys a curated pool and wants allowlisted users to be able to use the router (the normal UX path) will naturally allowlist the router address. This is the expected operational pattern, making the bypass reachable in any realistic curated-pool deployment that supports router-based swaps.

---

### Recommendation

Gate on the originating user, not the immediate pool caller. The cleanest fix is to add a `recipient`-or-`payer` parameter to the swap allowlist check, or to require the pool to forward the original initiator separately. A simpler short-term fix is to check `recipient` (the second argument already available in `beforeSwap`) when `sender` is a known router, but this is fragile. The correct invariant is:

> The allowlist must check the address that controls the economic decision to swap and that bears the token cost — which is the address that called the router, not the router itself.

One approach: extend the `extensionData` convention so the router encodes the originating user and the extension reads it from there, with a fallback to `sender` for direct pool calls.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for curated users.
3. Non-allowlisted user `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. `attacker` successfully trades against the curated pool despite never being in the allowlist.

Relevant call chain:

```
attacker → MetricOmmSimpleRouter.exactInputSingle()
              msg.sender of pool.swap() = router
           → MetricOmmPool.swap()
              _beforeSwap(sender=router, ...)
           → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              allowedSwapper[pool][router] == true  ← passes
           → swap executes for attacker
``` [6](#0-5) [7](#0-6) [4](#0-3)

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

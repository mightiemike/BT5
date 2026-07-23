### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Economic Actor, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router address to enable router-based swaps on a curated pool, every unprivileged user who calls through the router passes the allowlist check, completely defeating the curation policy.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router becomes `msg.sender` at the pool: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the pool admin allowlists the router address to let allowlisted users reach the pool through the router, the check passes for **every** caller of the router, not just the intended ones.

Contrast this with `DepositAllowlistExtension`, which correctly gates on `owner` (the LP position owner — the economically relevant actor) rather than `sender` (the caller of the pool): [5](#0-4) 

The swap extension has no equivalent distinction between the caller and the economic actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is fully bypassed. Any unprivileged user who calls through `MetricOmmSimpleRouter` trades on the pool as if they were allowlisted. This is a direct, fund-impacting policy failure: the pool's curation invariant — that only approved addresses may swap — is silently violated on every router-mediated trade.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural operational step: allowlisted users who want multi-hop swaps or slippage protection through the router cannot use it unless the router itself is allowlisted (because the extension will see `sender = router` and reject them). The admin is therefore pushed toward allowlisting the router to make the pool usable, which opens the gate to all users. The precondition is semi-trusted but operationally foreseeable.

---

### Recommendation

Gate on the economic actor, not the intermediary caller. The swap extension should check the `recipient` or, better, require the pool to forward the original `tx.origin`-equivalent user. The cleanest fix mirrors the deposit extension: add a distinct `swapper` parameter that the pool populates from a trusted source (e.g., a transient-storage slot set by the router before calling the pool, analogous to how the router already stores payer context). Alternatively, document that the router address must never be allowlisted and enforce this at the extension level by reverting if `sender` is a known router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — pool sets `sender = address(router)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → passes.
6. Attacker's swap executes on the curated pool despite never being allowlisted.

The allowlist check that should have blocked step 6 is: [6](#0-5) 

It evaluates `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][attacker]` (false), so the revert never fires.

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

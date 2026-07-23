### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may trade against a curated pool. Its `beforeSwap` hook checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), the allowlist becomes a no-op: any user can bypass per-user gating by calling the public router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`** [1](#0-0) 

The hook receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is whatever the pool passed as the first argument to `beforeSwap`.

**How the pool populates `sender`**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [2](#0-1) 

`_beforeSwap` encodes that value as the `sender` argument forwarded to every extension: [3](#0-2) 

So `sender` = pool's `msg.sender` = whoever called `pool.swap()`.

**How the router calls the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly: [4](#0-3) 

The router does **not** forward the originating user's address into the pool call. The pool's `msg.sender` is the router contract. Therefore, the extension sees `sender = router`, not `sender = user`.

**Contrast with `DepositAllowlistExtension`**

The deposit allowlist correctly gates the `owner` parameter (the position owner, which the adder explicitly sets to the actual user): [5](#0-4) 

`addLiquidity` accepts `owner` as an explicit argument separate from `msg.sender`, so the adder can pass the real user. `swap` has no such "owner" parameter — the only identity available to the extension is `sender` (the pool's `msg.sender`), which collapses to the router when the periphery is used.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses) must also allowlist the router if they want standard periphery access. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who calls the router, regardless of whether that user is individually permitted. Any unpermissioned user can drain value from the curated pool's liquidity by routing through the public `MetricOmmSimpleRouter`, bypassing the intended access control entirely. This is a direct loss of LP principal on pools whose security model depends on the allowlist.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who wants to support normal user flows will allowlist the router. The bypass requires no special privileges, no flash loans, and no exotic token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the intermediary. Two options:

1. **Pass the originating user through the pool**: Add a `swapper` or `payer` field to the swap call path (analogous to how `addLiquidity` separates `owner` from `msg.sender`) so the extension can check the real initiator.

2. **Check `recipient` as a proxy**: If the pool's design guarantees that the recipient is always the user, the extension could check `recipient` instead of `sender`. However, this is fragile because `recipient` can be set to any address.

The cleanest fix mirrors the deposit path: the pool should forward the originating user's address as a distinct parameter to swap extensions, separate from the caller (`msg.sender`).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router path
  - Pool admin does NOT allowlist attacker address
  - Pool admin adds liquidity from allowlisted LP

Attack:
  - Attacker (not in allowedSwapper) calls:
      router.exactInputSingle({pool: curatedPool, ...})
  - Router calls pool.swap(...) → pool's msg.sender = router
  - _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] = true → PASSES
  - Attacker executes swap against curated pool's liquidity
  - Allowlist is completely bypassed
```

The check that should have blocked the attacker — `allowedSwapper[pool][attacker]` — is never evaluated. The extension evaluates `allowedSwapper[pool][router]` instead, which is `true` by necessity of the pool's configuration.

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

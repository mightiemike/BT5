### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the router is allowlisted (the natural configuration for a curated pool that also wants to support periphery routing), every user on the network can bypass the allowlist by calling the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with its own `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument forwarded to every configured extension: [2](#0-1) 

**Step 2 — The router calls `pool.swap()` directly, so the pool sees `msg.sender` = router.**

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls `pool.swap()` without any mechanism to forward the original caller's address: [3](#0-2) 

**Step 3 — The extension checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` = router and checks `allowedSwapper[pool][router]`: [4](#0-3) 

The allowlist is keyed by `(pool, swapper)` and is intended to gate individual users: [5](#0-4) 

---

### Impact Explanation

Two fund-impacting outcomes follow directly from this wrong-actor binding:

**Outcome A — Complete allowlist bypass (High).**
A pool admin who wants allowlisted users to be able to use the standard periphery router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][sender]` passes for every caller regardless of their individual allowlist status. Any unprivileged user can call `exactInputSingle` (or any other router entry point) and trade on a pool that was designed to be curated. This is a direct loss of the curation invariant and enables unauthorized trading that the LP depositors did not consent to.

**Outcome B — Broken core swap functionality for allowlisted users (Medium).**
If the admin does not allowlist the router, every allowlisted user who attempts to swap through the router is rejected (`allowedSwapper[pool][router]` is false), even though their own address is individually allowlisted. The router — the primary supported periphery path — becomes unusable for the pool's intended participants.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is a production periphery extension explicitly documented as gating `swap` by swapper address per pool. The `MetricOmmSimpleRouter` is the primary supported swap entry point. Any pool admin who deploys both together and wants their allowlisted users to use the router will encounter Outcome B immediately, and the natural remediation (allowlisting the router) triggers Outcome A. The trigger requires only a standard `exactInputSingle` call from any address — no privileged access, no special token, no malicious setup.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary contract. Two viable fixes:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add an `originalSender` field to the swap hook signature**: The pool stores the original caller in transient storage at entry and passes it as a dedicated argument to every extension, separate from the pool-level `sender`.

The `DepositAllowlistExtension` does not share this bug because `addLiquidity` accepts an explicit `owner` parameter that the liquidity adder passes through correctly: [6](#0-5) 

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension configured in beforeSwap slot
  admin = pool admin
  alice = allowlisted user  (allowedSwapper[pool][alice] = true)
  bob   = non-allowlisted user

Step 1: admin calls setAllowedToSwap(pool, router, true)
        // necessary so alice can use the router; triggers the bypass

Step 2: bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
        // router calls pool.swap(); pool sees msg.sender = router
        // extension checks allowedSwapper[pool][router] == true  ✓
        // bob's swap executes on the curated pool despite not being allowlisted

Invariant broken:
  allowedSwapper[pool][bob] == false   (admin never allowlisted bob)
  bob successfully traded              (allowlist did not gate him)
```

The attack requires zero privileged access: `bob` is an ordinary EOA calling the public router entry point.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

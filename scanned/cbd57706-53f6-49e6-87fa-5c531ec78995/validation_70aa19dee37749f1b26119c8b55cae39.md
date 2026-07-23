### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to work on the curated pool), every user — including non-allowlisted ones — can bypass the per-user gate by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` of `pool.swap()` is the **router address**, so `sender` delivered to the extension is the router, not the end user. The pool admin faces an impossible choice:

- **Allowlist the router** → every user can bypass the per-user gate by routing through it.
- **Do not allowlist the router** → no user, including legitimately allowlisted ones, can use the router at all.

The `DepositAllowlistExtension` does **not** share this flaw because it checks the explicit `owner` argument (the position owner), not `sender` (the caller): [5](#0-4) 

The pool's `addLiquidity()` passes the caller-supplied `owner` separately from `msg.sender`, so the deposit allowlist always gates the economically relevant actor regardless of who pays. The swap path has no equivalent separate "owner" field — only `sender` (the direct caller) and `recipient` (the output destination).

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to enforce KYC, compliance, or access-control policies is completely defeated for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user receives the full swap output (`recipient` is set to the user by the router), while the allowlist check passes because it sees the allowlisted router address. This is a direct policy bypass with fund-flow consequences: value flows to actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user aware of the router address can exploit this without any privileged access. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want any of their allowlisted users to use the router. The bypass is therefore reachable on every curated pool that supports router-mediated swaps.

---

### Recommendation

The extension must gate the actual end user, not the intermediary. Two complementary approaches:

1. **Check `recipient` instead of (or in addition to) `sender`** when `sender` is a known router. This is imperfect for multi-hop flows where intermediate recipients are the router itself.

2. **Require the router to forward the real user identity through `extensionData`** and have the extension decode and verify it. The router would encode `msg.sender` (the EOA) into `extensionData` before calling `pool.swap()`, and the extension would verify that the decoded address is allowlisted. This requires the extension and router to agree on an encoding, but it is the only approach that correctly identifies the end user in all routing scenarios.

The `DepositAllowlistExtension` pattern — checking the explicit `owner` field rather than `sender` — is the correct model. The swap interface should be extended with an analogous "swapper owner" field, or the router must be made to inject the real user identity in a verifiable way.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // needed for router to work
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: bob,          // bob receives the output
       ...
     })
  2. Router calls pool.swap(bob, ...) → msg.sender of pool.swap() = router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
  5. Swap executes; bob receives output tokens.

Result: bob, a non-allowlisted address, successfully swaps on a curated pool,
        bypassing the SwapAllowlistExtension entirely.
```

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

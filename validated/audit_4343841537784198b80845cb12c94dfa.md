### Title
SwapAllowlistExtension checks router address as `sender` instead of actual end-user, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks the router's address — not the actual end-user. If the router is allowlisted (the natural setup for a pool that wants to support router-based swaps), every user, including non-allowlisted ones, bypasses the guard entirely.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle(...)
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder(...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

**Pool passes `msg.sender` as `sender` to every extension hook:** [1](#0-0) [2](#0-1) 

**The extension keys its allowlist on `sender` (the pool's `msg.sender`):** [3](#0-2) 

**The router calls `pool.swap(...)` directly, so the pool's `msg.sender` is always the router:** [4](#0-3) [5](#0-4) 

The same pattern holds for `exactOutputSingle` and `exactOutput`. [6](#0-5) 

**Two broken scenarios result:**

1. **Allowlist bypass (high impact):** Pool admin allowlists the router so that router-based swaps work. Because `allowedSwapper[pool][router] = true`, every user — including non-allowlisted ones — passes the check by routing through `MetricOmmSimpleRouter`. The per-user curation is completely defeated.

2. **Allowlisted users locked out of router (medium impact):** Pool admin does not allowlist the router. Allowlisted users who call the router get `NotAllowedToSwap` because `allowedSwapper[pool][router] = false`, even though their own address is allowlisted. The router is unusable on any curated pool.

There is no mechanism in the router to forward the actual caller's identity to the extension. The `extensionData` field is user-controlled but `SwapAllowlistExtension` never reads it. [7](#0-6) 

---

### Impact Explanation

A non-allowlisted user can trade on a curated pool that is supposed to restrict access to specific counterparties (e.g., KYC'd addresses, whitelisted market makers). The bypass is unconditional once the router is allowlisted, requires no special privileges, and is reachable through the standard public periphery path. This constitutes a direct loss of the pool admin's curation policy and, depending on the pool's purpose, can expose LPs to trades with unintended counterparties or allow extraction of value from pools designed for closed participant sets.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also wants to support the standard `MetricOmmSimpleRouter` faces this issue. The router is the primary public swap entrypoint documented in the periphery. A pool admin who allowlists the router (the only way to make the router work) immediately opens the pool to all users. The trigger requires no special timing, no privileged role, and no unusual token behavior — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must verify the actual end-user, not the intermediary. Two viable approaches:

1. **Pass user identity through `extensionData`:** Have the router encode `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and verify it. The extension must also verify that the encoding came from a trusted router (e.g., by checking `sender` is a known factory-registered router).

2. **Check `sender` only when called directly; decode user from `extensionData` when called via router:** The extension can distinguish direct calls (`sender` is an EOA or known contract) from router calls and apply the appropriate identity check.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed through the liquidity adder), not on `sender`. [8](#0-7) 

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so router-based swaps work.
// allowedSwapper[pool][router] = true
// allowedSwapper[pool][alice]  = true   (intended allowlisted user)
// allowedSwapper[pool][bob]    = false  (non-allowlisted user)

// Bob (non-allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: curated_pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));

// Inside the pool:
//   _beforeSwap(msg.sender=router, recipient=bob, ...)
//   SwapAllowlistExtension.beforeSwap(sender=router, ...)
//   allowedSwapper[pool][router] == true  → passes
//
// Bob's swap executes successfully despite not being allowlisted.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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

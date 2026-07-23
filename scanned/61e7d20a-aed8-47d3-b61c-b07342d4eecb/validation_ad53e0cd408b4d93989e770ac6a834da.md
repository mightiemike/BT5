### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end user. A pool admin who allowlists the router to enable periphery usage inadvertently opens the gate to every user on the network.

---

### Finding Description

**Root cause — wrong identity in the hook argument**

In `MetricOmmPool.swap()`, the `_beforeSwap` hook is dispatched with `sender = msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist: [3](#0-2) 

**What the router actually sends**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly: [4](#0-3) 

Inside the pool, `msg.sender` is the router contract. Therefore `sender` forwarded to `beforeSwap` is the router address — not the EOA who called the router.

**The two broken outcomes**

| Router allowlisted? | Allowlisted EOA via router | Non-allowlisted EOA via router |
|---|---|---|
| Yes (admin enables periphery) | Passes ✓ | **Passes — bypass** |
| No | **Reverts — broken** | Reverts |

If the admin allowlists the router so that legitimate users can use the periphery, every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`. If the admin does not allowlist the router, allowlisted users cannot use the periphery at all.

**Contrast with `DepositAllowlistExtension`**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner, explicitly passed by the pool), not `sender`: [5](#0-4) 

The swap interface has no equivalent "actual swapper" argument separate from `sender`, so the swap extension has no correct identity to check when an intermediary is involved.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter`. The allowlist guard — the only access-control mechanism on the swap path — provides no protection once the router is allowlisted. Trades that should be blocked execute normally, draining pool liquidity to unauthorized parties.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract; any EOA can call it.
- A pool admin who deploys a pool with `SwapAllowlistExtension` and wants users to interact via the standard periphery **must** allowlist the router — there is no other supported path.
- The bypass requires zero privileged access, no special tokens, and no unusual setup: one router call is sufficient.

---

### Recommendation

The `SwapAllowlistExtension` must gate the actual end user, not the intermediary. Two viable approaches:

1. **Router-attested identity via `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. The extension must also verify that `sender` (the caller of the pool) is a trusted router registered with the factory, so the attested identity cannot be forged by a malicious caller.

2. **Direct-only allowlist**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is not an EOA (check `sender == tx.origin`). This is simpler but prevents all router usage on allowlisted pools.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)      // alice is the intended grantee
  admin calls setAllowedToSwap(pool, router, true)     // admin enables periphery for alice

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: bob,
        ...
    })

  Router calls pool.swap(...) — msg.sender in pool = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap:
    allowedSwapper[pool][router] == true  →  no revert
  Bob's swap executes successfully.

Result: Bob, who is not on the allowlist, completes a swap against a pool
        that was intended to be restricted to alice only.
``` [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
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

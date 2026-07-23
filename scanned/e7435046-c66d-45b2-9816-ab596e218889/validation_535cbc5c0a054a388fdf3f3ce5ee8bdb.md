### Title
Swap Allowlist Bypass via Router: `SwapAllowlistExtension` Checks Router Address Instead of Actual User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` mediates a swap, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not the actual user. This is the direct analog of the Solana whitelist bypass: a required binding check is missing, allowing the wrong identity to satisfy the guard.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the end user: [4](#0-3) 

So the allowlist check resolves to `allowedSwapper[pool][router]`. The actual user's address is never consulted.

This creates two mutually exclusive failure modes:

1. **Bypass (High):** If the pool admin allowlists the router (a natural action so that allowlisted users can use the router), every user — including those not individually allowlisted — can swap freely through the router. The allowlist is completely defeated.
2. **DoS (Medium):** If the admin does not allowlist the router, every router-mediated swap reverts with `NotAllowedToSwap`, even for individually allowlisted users. The router is unusable on allowlisted pools.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

The `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` parameter (the LP recipient), which is explicitly passed by the caller and correctly identifies the economic actor regardless of routing. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. The attacker receives pool output tokens and pays input tokens exactly as an allowlisted user would. The pool's curation invariant is broken, and any value-protection or compliance purpose of the allowlist is nullified.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the periphery. Any pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to be able to use the router must allowlist the router — triggering the bypass. The attacker needs no special privileges: a standard `exactInputSingle` call through the public router is sufficient.

---

### Recommendation

The extension must identify the **original user**, not the intermediate router. Two sound approaches:

1. **Pass the original sender through `extensionData`:** The router encodes `msg.sender` (the end user) into `extensionData` for each hop, and the extension decodes and checks it. The pool's `onlyPool` guard on the extension already ensures only a legitimate pool can invoke the hook.

2. **Add a `recipient`/`originator` field to the swap interface:** The pool passes both the direct caller and an explicit originator to the extension, and the extension checks the originator.

Either way, the extension must verify the identity of the economically relevant actor — the user whose funds are being swapped — not the address of the routing contract.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the router.
3. Attacker (address not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle` with the restricted pool as target.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap executes.
5. Attacker receives output tokens from a pool they were never supposed to access.

Alternatively, if the admin does not allowlist the router:

3. Allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle`.
4. Extension checks `allowedSwapper[pool][router]` → `false`. Reverts with `NotAllowedToSwap`.
5. The router is permanently unusable for this pool, even for legitimate users.

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

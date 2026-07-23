### Title
`SwapAllowlistExtension` gates on the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user. Any pool admin who allowlists the router (required for allowlisted users to use the router) simultaneously opens the gate for every non-allowlisted user.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` to the pool: [4](#0-3) 

So the extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`. The actual user's address is never checked.

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly ignores `sender` and gates on `owner` — the actual position beneficiary: [5](#0-4) 

The asymmetry is structural: the deposit guard keys on the economically relevant actor (`owner`); the swap guard keys on the transport layer (`sender`/router).

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants allowlisted users to be able to trade through the standard router must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, every non-allowlisted address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and pass the guard, because the extension sees `sender = router` (allowlisted) and never inspects the actual caller. The allowlist policy is completely nullified for all router-mediated swaps. This is a direct curation failure on pools designed to restrict trading to specific counterparties (e.g., KYC pools, institutional pools).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who configure `SwapAllowlistExtension` and want their allowlisted users to trade normally will inevitably allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — any address can call `exactInputSingle` on the router at any time.

---

### Recommendation

Replace the `sender`-based check in `SwapAllowlistExtension.beforeSwap` with a check on the `recipient` parameter, or — more robustly — require the actual user identity to be passed through `extensionData` and verified against a signature or a trusted forwarder pattern. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap()` calls only, and enforce this at the factory level.

A minimal fix consistent with the deposit extension pattern would be to check `recipient` instead of `sender`:

```solidity
// current (broken for router flows):
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) { ... }

// candidate fix — gate on the economic beneficiary:
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) { ... }
```

Note that `recipient` is also caller-controlled, so a complete fix requires a trusted-forwarder or signed-identity approach if strict per-user gating is required.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. `_beforeSwap(router, bob, ...)` is dispatched; the extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite never being allowlisted.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

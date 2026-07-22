### Title
SwapAllowlistExtension Wrong-Actor Binding: Router-Mediated Swaps Bypass Per-User Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router (required for any user to use the router with this pool), every user — including those not individually allowlisted — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` is the pool (correct). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap()` call: [2](#0-1) 

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So the call chain is:

```
user → router.exactInputSingle()
         → pool.swap()          [msg.sender = router]
             → _beforeSwap(sender = router, ...)
                 → extension.beforeSwap(sender = router)
                     → allowedSwapper[pool][router]  ← checks router, not user
```

The extension never sees the actual end user. It only sees the router address.

**The dilemma this creates for pool admins:**

| Admin choice | Effect |
|---|---|
| Do NOT allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | Every user (including non-allowlisted ones) can bypass the guard via the router |

There is no configuration that simultaneously allows specific users to use the router while blocking others. The guard is structurally bypassed whenever the router is allowlisted.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is typically deployed to restrict swap counterparties — for example, to prevent adverse selection by limiting swaps to known, trusted market participants. If the router is allowlisted (which is required for any allowlisted user to use the router), any unprivileged user can:

1. Call `router.exactInputSingle` targeting the curated pool.
2. The extension sees `sender = router` (allowlisted), passes the check.
3. The unauthorized user executes a swap at the oracle-anchored price.

LP funds are directly exposed to adverse selection from counterparties the allowlist was designed to exclude. This is a direct loss of LP principal — the exact impact the allowlist guard was deployed to prevent.

---

### Likelihood Explanation

- Any user can call `MetricOmmSimpleRouter` — it is a public, permissionless periphery contract.
- The only precondition is that the pool admin has allowlisted the router (a natural and expected configuration for any pool that intends to support router-mediated swaps for its allowlisted users).
- No special privileges, flash loans, or complex setup are required.

---

### Recommendation

The `beforeSwap` hook should check the **end user** identity, not the direct caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `sender` and fall back to a router-forwarded identity**: Extend the extension interface so that trusted routers forward the originating user address, and the extension validates that forwarded identity against the allowlist.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used with direct pool calls, or to redesign the extension to accept a signed/forwarded user identity from trusted routers.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Admin allowlists `userA` and the `MetricOmmSimpleRouter` (so `userA` can use the router).
3. `userB` (not allowlisted) calls `router.exactInputSingle({ pool: curatedPool, ... })`.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] == true` → passes.
6. `userB`'s swap executes against the curated pool's LP liquidity.
7. LPs suffer losses from an unauthorized counterparty the allowlist was designed to exclude. [5](#0-4) [6](#0-5) [4](#0-3)

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

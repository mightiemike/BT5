### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. If the pool admin allowlists the router (the natural configuration for pools that want to support router-based swaps), every user — including those not individually allowlisted — can bypass the swap gate by routing through the public router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (used as the namespace key) and `sender` is the first argument the pool passes into the hook.

**How the pool populates `sender`**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

**How the router calls the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The broken invariant**

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`:

| Step | `msg.sender` seen by pool |
|---|---|
| User → Router | `user` |
| Router → `pool.swap()` | `router` |
| Pool → `beforeSwap(sender=router, ...)` | `router` |
| Extension checks `allowedSwapper[pool][router]` | checks **router**, not **user** |

The allowlist check is against the router's address, not the actual user's address. The actual user's address is never visible to the extension.

**Contrast with `DepositAllowlistExtension`**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner), not `sender`:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [6](#0-5) 

`MetricOmmPoolLiquidityAdder` passes the actual user as `owner` when calling `pool.addLiquidity(owner, ...)`, so the deposit allowlist correctly gates the economic actor. The swap allowlist has no equivalent — it only sees the immediate caller of `pool.swap()`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To also allow those users to swap through the supported `MetricOmmSimpleRouter`, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check passes for **any** user who routes through it — including users who are individually blocked. The allowlist policy is completely bypassed for router-mediated swaps, allowing disallowed users to trade against the pool's liquidity.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint for the protocol. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router, which simultaneously opens the gate to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call.

---

### Recommendation

The extension must gate the **originating user**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the originating user through the router**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a trust assumption that the extension only accepts this encoding from the router.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the actual user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Preferred — remove router indirection**: Require that allowlisted pools are only accessed directly (not through the router), and document this constraint clearly. Alternatively, the pool admin should never allowlist the router address; instead, each individual user must be allowlisted and must call the pool directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists router: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist alice: allowedSwapper[pool][alice] == false

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: alice, ...})
  2. Router calls pool.swap(alice, zeroForOne, amount, limit, "", extensionData)
     → msg.sender of pool.swap() = router
  3. Pool calls _beforeSwap(sender=router, recipient=alice, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
  5. Swap executes; alice receives output tokens

Result: alice, who is not on the allowlist, successfully swaps against the curated pool.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

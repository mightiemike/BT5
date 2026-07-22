### Title
`SwapAllowlistExtension` Checks Router Address Instead of Economic Actor, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users inadvertently opens the gate to every user, because the router is a public, permissionless contract.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`, the router calls `pool.swap()` with `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. But `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it. Allowlisting the router therefore grants every user the ability to swap in the restricted pool, defeating the allowlist entirely.

Critically, there is **no way** for the pool admin to achieve "allow my allowlisted users to use the router" without simultaneously allowing every user to bypass the allowlist. The router does not forward the originating user's identity to the pool.

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. If the pool admin has restricted swaps for economic reasons (e.g., to prevent adversarial flow, enforce counterparty vetting, or comply with regulatory requirements), the bypass allows unrestricted trading against LP positions. LPs in such pools suffer the exact adverse-selection or compliance exposure the allowlist was meant to prevent.

---

### Likelihood Explanation

Medium. The bypass is only reachable after the pool admin allowlists the router — a deliberate but reasonable action for any admin who wants their allowlisted users to benefit from the router's UX (deadline checks, multi-hop, etc.). The misunderstanding is natural: the admin believes the allowlist still gates individual users when the router is allowlisted, but it does not. Once the router is allowlisted, the trigger is fully unprivileged and requires no further admin cooperation.

---

### Recommendation

The extension must gate the **economic actor**, not the immediate caller. Two viable approaches:

1. **Check `recipient` in addition to `sender`**: For a swap allowlist, the recipient of pool output is the economic beneficiary. Requiring both `sender` and `recipient` to be allowlisted raises the bar, though it does not fully solve the problem for multi-hop routes.

2. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and the extension, and the extension must reject calls where `extensionData` is absent or malformed.

3. **Document that the router must never be allowlisted on restricted pools**: If the design intent is that router-mediated swaps are simply incompatible with the allowlist, this must be stated explicitly in the extension's NatSpec and enforced by the factory or a deployment check.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured; sets `allowAllSwappers[pool] = false`.
2. Admin allowlists `user1`: `allowedSwapper[pool][user1] = true`.
3. Admin allowlists the router so `user1` can use it: `allowedSwapper[pool][router] = true`.
4. `user2` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)` — `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → no revert.
7. `user2` successfully swaps in the restricted pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router (necessary for any allowlisted user to use the router), every user — including non-allowlisted ones — can bypass the guard by calling `exactInputSingle` / `exactInput` / `exactOutput` on the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to the extension:

```solidity
// MetricOmmPool.sol L230-L240
_beforeSwap(
  msg.sender,   // <-- immediate caller of pool.swap()
  recipient,
  ...
  extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` of `pool.swap()` is the router, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position owner explicitly passed by the caller), not `sender`.

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., KYC-gated, institutional-only, or compliance-restricted) and configures `SwapAllowlistExtension` to gate by user identity must also allowlist the router if any approved user is expected to use the periphery. Once the router is allowlisted, the check degenerates to "is the router allowed?" — which is always true for every user who calls through it. Non-allowlisted users can freely swap in the restricted pool, draining LP funds at oracle-anchored prices the LPs deposited under the assumption of a restricted counterparty set.

---

### Likelihood Explanation

The scenario is directly reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any pool where approved users are meant to use the standard periphery. The router is a public, permissionless contract with no access control of its own. No privileged action, malicious setup, or non-standard token is required.

---

### Recommendation

The extension should gate by the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router**: The router should forward `msg.sender` (the end user) as part of `extensionData` or as a dedicated field, and the extension should decode and check that identity.

2. **Alternatively, check `sender` only when `sender` is not a known router**: The extension could maintain a registry of trusted routers and, when `sender` is a router, require the router to attest the real user identity in `extensionData`.

The simplest safe fix is to have the pool pass the original user identity through a dedicated channel (e.g., a `payer` field in the swap interface) so extensions can always gate on the true economic actor regardless of routing depth.

---

### Proof of Concept

1. Admin deploys pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in a pool he is not authorized to access, draining LP funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

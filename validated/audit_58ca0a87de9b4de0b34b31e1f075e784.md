### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any caller to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the address the pool received as `msg.sender` when `swap` was called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted, not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the gate to every caller on the router, defeating the allowlist entirely.

---

### Finding Description

**Pool → Extension identity chain**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**Router path**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The bypass**

A pool admin who wants KYC'd or whitelisted users to be able to use the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every caller of the router — including completely non-allowlisted addresses — passes the check, because the extension never sees the actual end user's address.

**Contrast with DepositAllowlistExtension**

`DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the explicit position-owner parameter):

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`swap` has no equivalent explicit "end user" parameter; the pool only exposes `msg.sender` (the immediate caller) as `sender`.

---

### Impact Explanation

Any non-allowlisted address can swap against a pool that uses `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`, as long as the pool admin has allowlisted the router (a natural step when the admin wants approved users to be able to use the router). The allowlist — the pool's primary access-control mechanism — is silently bypassed. Non-approved counterparties can drain pool liquidity, extract value from LP positions, or interact with a pool that was intended to be restricted (e.g., for regulatory compliance or private market-making). This is a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural and expected administrative action: any pool that uses `SwapAllowlistExtension` and also wants its approved users to be able to use the standard router must allowlist the router. The admin has no indication that doing so opens the gate to all callers. The bypass is then reachable by any unprivileged address with no special setup.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate the actual end user, not the immediate caller. Two options:

1. **Decode the real user from `extensionData`**: require the router to encode `msg.sender` (the actual user) into `extensionData` and have the extension decode and check it. This requires a coordinated change to the router and the extension.

2. **Mirror the deposit pattern**: add an explicit `swapper` parameter to the `swap` call (analogous to `owner` in `addLiquidity`) so the pool can pass the intended beneficiary separately from the immediate caller. The extension then checks that parameter instead of `sender`.

Until fixed, pool admins should not allowlist the router address on pools that use `SwapAllowlistExtension` with a restricted set of approved swappers.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists user1: allowedSwapper[pool][user1] = true
  - Pool admin allowlists router: allowedSwapper[pool][router] = true
    (so user1 can use the router)

Attack:
  - attacker (not in allowlist) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - router calls pool.swap(...) with msg.sender = router
  - pool calls extension.beforeSwap(router, ...) with sender = router
  - extension checks allowedSwapper[pool][router] → true → passes
  - attacker's swap executes against the pool's LP liquidity

Result:
  - attacker bypasses the allowlist and swaps against a restricted pool
  - LP funds are exposed to an unapproved counterparty
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

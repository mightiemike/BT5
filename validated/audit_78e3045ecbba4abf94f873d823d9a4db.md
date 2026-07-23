Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` equals the router address. A pool admin who allowlists the router to enable router-based swaps inadvertently grants swap access to every user of the router, completely bypassing the per-user allowlist.

## Finding Description

**Root cause in `MetricOmmPool.swap`:** The pool dispatches the before-swap hook passing `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // sender = direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension: [2](#0-1) 

**Root cause in `SwapAllowlistExtension.beforeSwap`:** The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Router as direct pool caller:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of users (e.g., KYC'd addresses).
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-based swaps for allowlisted users.
3. Any unprivileged user (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The router calls `pool.swap()` — `msg.sender` inside the pool is the router.
5. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] = true`, and passes.
6. The non-allowlisted user's swap executes successfully, bypassing the per-user allowlist entirely.

**Exact wrong value:** `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][end_user]`. The extension decision (allow/block) is wrong because the identity checked is the router, not the economically relevant actor.

**Existing guards insufficient:** There is no field in the `beforeSwap` signature that carries the original end-user's address. The `recipient` parameter is the output recipient, not the initiating user. The `BaseMetricExtension.onlyPool` modifier only verifies the caller is a valid pool — it does not help recover the original user identity. [6](#0-5) 

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control mechanism for restricting pool swaps to authorized addresses. Its bypass allows any unprivileged user to swap in a pool the admin intended to restrict, breaking the pool's core swap-gating functionality. This constitutes broken core pool functionality and an admin-boundary break via an unprivileged path (any router caller). Pools configured for permissioned access (e.g., compliance-gated, institutional, or whitelist-only pools) are rendered fully open to the public whenever the router is allowlisted.

## Likelihood Explanation
The condition requires the pool admin to have allowlisted the router address — a natural and expected action for any pool that intends to support router-based swaps. Once that single admin action is taken, the bypass is unconditionally reachable by any address that calls the router. No special privileges, flash loans, or timing are required. The attack is repeatable on every swap.

## Recommendation
The extension must gate on the true end-user identity, not the direct pool caller. Two approaches:

1. **Pass original caller through `extensionData`:** The router encodes `msg.sender` (the end-user) into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension.beforeSwap` decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` as the gated identity:** If the pool's design intent is that the swap beneficiary is the entity to gate, use `recipient` instead of `sender`. However, `recipient` can also be set to an arbitrary address, so this only works if the pool's threat model aligns.

3. **Disallow router allowlisting / document the invariant:** Document that allowlisting the router is equivalent to `allowAllSwappers = true`, and provide a separate mechanism for router-mediated per-user checks (e.g., a callback into the router to verify the original caller).

The cleanest fix is option 1: have the router always encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it when present, falling back to `sender` for direct pool calls.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension configured
// 2. Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
// 3. Attacker (not in allowlist) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// 4. pool.swap() is called with msg.sender = router
// 5. beforeSwap receives sender = router, checks allowedSwapper[pool][router] = true → passes
// 6. Attacker's swap executes despite not being in the allowlist
```

Foundry test: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only the router, then assert that a non-allowlisted EOA can successfully swap via the router but reverts when calling `pool.swap()` directly.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

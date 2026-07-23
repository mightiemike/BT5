### Title
`SwapAllowlistExtension` checks direct pool caller (`sender`) instead of ultimate user, enabling any unprivileged user to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking `sender`, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for their curated users, any unprivileged user can bypass the per-user allowlist by calling the pool through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is whoever called `pool.swap()`. [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the ultimate user
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` of that call:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

So when any user calls the router, the extension sees `sender = router`, not the actual user. The extension then evaluates `allowedSwapper[pool][router]`.

A pool admin who wants to:
1. Restrict swaps to specific KYC'd users (allowlist A, B, C)
2. Allow those users to use the router (a natural UX expectation)

…must allowlist the router address. But the router is a permissionless public contract — any user can call it. Allowlisting the router therefore grants swap access to **all** users, not just A, B, C. The extension cannot distinguish between different users going through the same router instance.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner, which is economically meaningful and caller-supplied per-deposit), not `sender`. [4](#0-3) 

---

### Impact Explanation

Any user not on the per-user allowlist can swap on a curated pool by routing through `MetricOmmSimpleRouter`. This breaks the pool's intended curation policy (e.g., KYC/AML compliance, institutional-only pools). The attacker receives real token output from the pool at oracle-anchored prices, constituting a direct policy bypass with fund-impacting consequences for the pool's LP base and regulatory posture.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a realistic and expected configuration: a pool admin who wants their allowlisted users to benefit from the router's multi-hop and exact-output features must allowlist the router. The mistake is non-obvious because the admin may believe they are granting router access only to their curated users, not realizing the router is a public contract callable by anyone. The `MetricOmmSimpleRouter` has no access control of its own. [5](#0-4) 

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **ultimate user**, not the direct pool caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router or a signed payload.
2. **Check `sender` only for direct calls; require a signed user identity for router calls**: The extension inspects whether `sender` is a known router and, if so, reads the actual user from a verified field in `extensionData`.

Additionally, document clearly that allowlisting the router address grants swap access to all users, not just individually allowlisted ones.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, userA, true)  — intends to allow only userA.
3. Pool admin calls setAllowedToSwap(pool, router, true) — intends to let userA use the router.
4. Non-allowlisted userD calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(...) — msg.sender of pool.swap() = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. ExtensionCalling dispatches to SwapAllowlistExtension.beforeSwap(sender=router, ...).
8. Extension checks allowedSwapper[pool][router] → true.
9. Swap executes. userD receives token output. Allowlist is bypassed.
```

The reentrancy guard (`MetricReentrancyGuardTransient`) correctly blocks re-entry into the pool during the swap callback, so no additional pool-state manipulation is possible within the same transaction. The bypass is purely at the allowlist identity-check layer. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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

**File:** metric-core/contracts/utils/MetricReentrancyGuardTransient.sol (L35-38)
```text
  function _nonReentrantBefore(uint256 actionId) private {
    _nonReentrantBeforeView();
    TransientSlot.tstore(TransientSlot.asUint256(_reentrancyGuardStorageSlot()), actionId);
  }
```

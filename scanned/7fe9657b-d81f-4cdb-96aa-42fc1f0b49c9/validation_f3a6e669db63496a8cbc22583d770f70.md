### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Per-User Swap Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router address (the only way to enable router-mediated swaps for any user), every unprivileged address can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `ExtensionCalling._callExtensionsInOrder`), and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`, which is `msg.sender` of the originating `pool.swap()` call. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

So `msg.sender` of `pool.swap()` = router address. The extension sees `sender` = router, not the actual end user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][user]`.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner passed as an explicit parameter), not `sender` (the operator/payer). This works because `addLiquidity` carries the actual beneficiary as a named argument. The swap path has no equivalent parameter for the real end user. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., whitelisted market makers or KYC'd counterparties). If the pool admin allowlists the router address — the only way to enable router-mediated swaps for any user — then every unprivileged address can bypass the per-user gate by calling any of the four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). Non-allowlisted users can execute swaps against the restricted pool, consuming LP liquidity at oracle prices in ways the pool admin explicitly intended to prevent. LP principals are at risk from counterparties the pool was designed to exclude.

---

### Likelihood Explanation

The trigger is fully unprivileged: any EOA or contract can call the public router. The precondition — the router being allowlisted — is the natural and necessary configuration for any pool that wants to support router-mediated swaps for its allowlisted users. A pool admin who allowlists individual users and also allowlists the router (to let those users trade via the router) inadvertently opens the gate to all users. This is a realistic and likely deployment scenario.

---

### Recommendation

Pass the original end user's address through the swap path so the extension can gate on it. Two approaches:

1. **Preferred:** Add a `swapper` field to the swap call or extension data that the router populates with `msg.sender` before calling `pool.swap()`. The extension checks this field instead of (or in addition to) `sender`.

2. **Alternative:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at pool creation time (e.g., revert if both the router and `SwapAllowlistExtension` are configured without `allowAllSwappers`).

Note that `DepositAllowlistExtension` does not share this flaw because `addLiquidity` carries `owner` as an explicit argument that the liquidity adder populates with the real beneficiary.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, userA, true)` to allowlist a specific user.
4. `userB` (not allowlisted) calls `router.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `_beforeSwap` passes `sender = router` to the extension.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. `userB` completes the swap against the restricted pool, bypassing the per-user allowlist.

The extension's check `allowedSwapper[msg.sender][sender]` at line 37 of `SwapAllowlistExtension.sol` resolves to `allowedSwapper[pool][router]`, which is `true`, so the revert is never triggered for any user routing through the router. [6](#0-5) [7](#0-6)

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
